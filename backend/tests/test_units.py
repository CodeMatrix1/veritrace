from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from components.candidate_selector import select_candidates
from components.claim_classifier import normalize
from components.claim_extractor import validation_error
from components.evidence_validator import validate_item
from components.number_compare import compare, parse_numbers
from components.passage_ranker import rank_passages
from components.report_writer import overall_result, sentence_is_grounded
from components.search_runner import SearchOutcome
from components.source_router import host_matches, is_blocked, source_tier
from components.candidate_selector import build_candidates
from components.sufficiency_checker import assess_sufficiency, independent_count
from components.text_extractor import merge_pdf_line_wraps, split_sentences
from config.loader import load_config
from pipeline.verdict_engine import VerdictInput, decide
from schemas.claim import AtomicClaim, ClaimClassification, KeyDetail
from schemas.document import Document, Passage, Sentence
from schemas.evidence import Evidence
from schemas.llm_outputs import ClassificationItem, EvidenceItem, ExtractedClaim
from schemas.search import Candidate, SearchHit, SearchRequest
from schemas.verdict import ClaimVerdict
from services.limits import AttemptBudget
from services.url_safety import canonicalize

CONFIG = load_config()


def claim(text="India's GDP grew by 8% in FY2024-25.", details=("India GDP", "8%", "FY2024-25"), **kw):
    key_details = [KeyDetail(id=f"c1_d{i}", text=d) for i, d in enumerate(details, 1)]
    numbers = [n for d in details for n in parse_numbers(d)]
    return AtomicClaim(
        id="c1", position=1, text=text, source_text=text, kind="factual",
        key_details=key_details, numbers=numbers, **kw,
    )


def document(sentences=("Some text.",), domain="mospi.gov.in", doc_id="d1", ref="doc_01", **kw):
    items = [Sentence(id=f"{ref}_s{i}", text=s) for i, s in enumerate(sentences, 1)]
    defaults = dict(
        id=doc_id, ref=ref, audit_id="a", canonical_url=f"https://{domain}/{doc_id}",
        domain=domain, title="India GDP report", content_type="html",
        content_hash=doc_id, text_snapshot=" ".join(sentences), sentences=items,
        language="en", published_at=None, published_raw=None,
        retrieved_at=datetime.now(timezone.utc),
    )
    return Document(**{**defaults, **kw})


def validate(item, a_claim, doc):
    passage = Passage(
        id="p", document_id=doc.id, sentence_ids=[s.id for s in doc.sentences],
        text="", bm25_score=0,
    )
    return validate_item(item, a_claim, [passage], {doc.id: doc})


def classification(domain="finance", claim_type="official_record", secondary=()):
    return ClaimClassification(
        claim_id="c1", primary_domain=domain, secondary_domains=list(secondary),
        claim_type=claim_type,
    )


# Numbers


@pytest.mark.parametrize("text,value,unit", [
    ("₹50,000", "50000", "INR"),
    ("Rs 50k", "50000", "INR"),
    ("2 lakh", "200000", None),
    ("5 crore", "50000000", None),
    ("₹2,00,000", "200000", "INR"),
    ("8.00%", "8.00", "percent"),
    ("3 percentage points", "3", "percentage_point"),
])
def test_numbers_parse(text, value, unit):
    fact = parse_numbers(text)[0]
    assert fact.value == Decimal(value)
    assert fact.unit == unit


def test_number_precision():
    assert parse_numbers("8%")[0].precision == 0
    assert parse_numbers("8.0%")[0].precision == 1
    assert parse_numbers("8.00%")[0].precision == 2


def test_years_and_ordinals_are_not_numbers():
    assert parse_numbers("in 2024 on the 3rd") == []


def fact(text):
    return parse_numbers(text)[0]


def test_rounding_rules():
    assert compare(fact("8%"), fact("7.6%")) == "rounded"
    assert compare(fact("8.0%"), fact("7.6%")) == "mismatch"
    assert compare(fact("exactly 8%"), fact("7.6%")) == "mismatch"
    assert compare(fact("exactly 8%"), fact("8%")) == "exact"


def test_half_up_boundary():
    assert compare(fact("8%"), fact("7.5%")) == "rounded"
    assert compare(fact("8%"), fact("8.5%")) == "mismatch"


def test_over_and_under_reject_equal_values():
    assert compare(fact("over 8%"), fact("8%")) == "mismatch"
    assert compare(fact("over 8%"), fact("9%")) == "match"
    assert compare(fact("under 8%"), fact("8%")) == "mismatch"
    assert compare(fact("under 8%"), fact("7%")) == "match"


def test_percent_and_points_differ():
    assert compare(fact("8%"), fact("8 percentage points")) == "different_unit"


# Claims


def extracted(**kw):
    defaults = dict(text="x", source_text="x", kind="factual", key_details=["x"])
    return ExtractedClaim(**{**defaults, **kw})


def test_extraction_rejects_invented_source_text():
    assert validation_error(extracted(source_text="not there"), "the input") is not None


def test_missing_figures_are_added_to_key_details():
    from components.claim_extractor import complete_key_details

    item = extracted(text="Riverton Bridge costs 500", source_text="Riverton Bridge costs 500", key_details=["Riverton Bridge"])
    assert validation_error(item, "Riverton Bridge costs 500") is None
    assert complete_key_details(item) == ["Riverton Bridge", "500"]


def test_year_followed_by_a_comma_is_not_a_figure():
    """Live-run regression: "August 2023," was read as a number, not a year."""
    assert parse_numbers("in August 2023, making it first") == []
    assert parse_numbers("cost ₹1,200, in total")[0].value == Decimal("1200")


@pytest.mark.parametrize("text", [
    "The mission was launched by NASA from Florida.", "It launched in 2019.",
    "This was built in Norway.", "The scheme has ended.", "They won the vote.",
])
def test_vague_subject_is_rejected(text):
    assert "does not name its subject" in validation_error(
        extracted(text=text, source_text="x"), "x"
    )


@pytest.mark.parametrize("text", [
    "The Riverton ferry was launched in 2019.", "The WHO declared an emergency.",
    "India launched a mission.", "Antibiotics cure infections.",
    "The Paris Agreement entered into force in 2016.",
])
def test_named_subject_is_accepted(text):
    assert validation_error(extracted(text=text, source_text="x"), "x") is None


def test_digit_in_a_name_is_not_a_required_number():
    """Live-run regression: the 3 in Chandrayaan-3 failed the audit."""
    text = "Apollo-3 landed, and it was the first."
    item = extracted(text=text, source_text=text, key_details=["first"])
    assert validation_error(item, text) is None


def test_equal_values_in_different_forms_match():
    item = extracted(source_text="fell to 5.5%", key_details=["5.5 per cent"])
    assert validation_error(item, "fell to 5.5%") is None


def test_comma_numbers_match_in_key_details():
    item = extracted(source_text="gives ₹50,000", key_details=["₹50,000"])
    assert validation_error(item, "gives ₹50,000") is None


def test_opinions_skip_factual_checks():
    assert validation_error(extracted(kind="not_checkable", text="", key_details=[]), "x") is None


def test_invalid_type_uses_the_strictest_standard():
    item = ClassificationItem(claim_index=0, primary_domain="bad", claim_type="bad")
    result = normalize("c1", item)
    assert result.primary_domain == "general_news"
    assert result.claim_type == "official_record"
    assert result.fallback is True


def test_missing_classification_requires_primary():
    assert normalize("c1", None).claim_type == "official_record"


# Sources


def test_host_matching_is_label_bounded():
    assert host_matches("eci.gov.in", "gov.in")
    assert host_matches("gov.in", "gov.in")
    assert not host_matches("example-gov.in", "gov.in")


@pytest.mark.parametrize("host,tier", [
    ("who.int", "primary"),
    ("mospi.gov.in", "primary"),
    ("spb.kerala.gov.in", "primary"),
    ("usgs.gov", "primary"),
    ("irs.gov", "unknown"),
    ("gov.uk", "unknown"),
    ("rbi.org.in", "primary"),
    ("example.com", "unknown"),
    ("example-gov.in", "unknown"),
    ("govt.example.com", "unknown"),
    ("gov.in.example.com", "unknown"),
    ("ddnews.gov.in", "secondary"),
])
def test_official_sites_are_found_by_pattern(host, tier):
    assert source_tier(host, CONFIG) == tier


def test_blocked_hosts():
    assert is_blocked("www.pinterest.com", CONFIG)


def test_config_rejects_bad_yaml(tmp_path: Path):
    (tmp_path / "evidence_rules.yaml").write_text('version: "1"\npolicy:\n  bad_key: 1\n')
    (tmp_path / "sources.yaml").write_text('version: "1"\nauthority: []\n')
    with pytest.raises(ValidationError):
        load_config(tmp_path)


def test_url_canonicalization():
    assert (
        canonicalize("HTTPS://Example.com/a/?utm_source=x&id=7&fbclid=y#top")
        == "https://example.com/a?id=7"
    )


# Selection


def candidate(url, domain, score, tier="unknown", origins=("support",)):
    hit = SearchHit(
        search_id="s", url=url, canonical_url=url, domain=domain, title="", snippet="", rank=1
    )
    return Candidate(hit=hit, tier=tier, score=score, origins=set(origins))


def test_slots_keep_contradiction_coverage():
    items = [candidate(f"u{i}", f"d{i}.com", 10 - i) for i in range(6)]
    items.append(candidate("c", "c.com", 0.1, origins=("contradiction",)))
    selected, replacements = select_candidates(items)
    assert len(selected) == 5
    assert any("contradiction" in c.origins for c in selected)
    assert len(replacements) == 2


def test_two_per_domain_limit():
    items = [candidate(f"u{i}", "same.com", 5 - i) for i in range(5)]
    selected, replacements = select_candidates(items)
    assert len(selected) == 2
    assert len(replacements) == 3


def test_merged_url_keeps_both_origins():
    hit = SearchHit(
        search_id="s", url="https://a.com/x?utm_source=1", canonical_url="https://a.com/x",
        domain="a.com", title="", snippet="", rank=1,
    )
    outcomes = [
        SearchOutcome(SearchRequest("c1", p, "open", "q"), "s", "ok", False, [hit])
        for p in ("support", "contradiction")
    ]
    candidates, records = build_candidates(outcomes, claim(), CONFIG)
    assert len(candidates) == 1
    assert candidates[0].origins == {"support", "contradiction"}
    assert records[0].decision == "merged"


# Passages


def test_passage_limits_and_origin_coverage():
    high = document(
        [f"India GDP grew 8% in FY2024-25 point {i}." for i in range(30)], doc_id="d1"
    )
    low = document(
        [f"Weather was mild and GDP notes {i}." for i in range(30)], doc_id="d2", ref="doc_02"
    )
    passages = rank_passages(
        [(high, frozenset({"support"})), (low, frozenset({"contradiction"}))], claim()
    )
    assert sum(p.document_id == "d1" for p in passages) <= 2
    assert any(p.document_id == "d2" for p in passages)


def test_sentence_split_keeps_abbreviations():
    assert split_sentences("Dr. Rao spoke. It rained.") == ["Dr. Rao spoke.", "It rained."]


def test_pdf_line_wrap_is_merged_not_split():
    """A single newline from PDF text wrapping must not break a sentence in half,
    as happened in a live audit: 'growth rates\\nof 7.1%' lost 'growth rates'."""
    text = (
        "The Economy has exhibited sustained performance, recording Real GDP growth rates\n"
        "of 7.2% and 7.1% respectively during FY 2023-24 and FY 2024-25.\n"
        "Nominal GDP has registered 11.0% growth during FY 2023-24."
    )
    sentences = split_sentences(merge_pdf_line_wraps(text))
    assert sentences[0] == (
        "The Economy has exhibited sustained performance, recording Real GDP growth rates "
        "of 7.2% and 7.1% respectively during FY 2023-24 and FY 2024-25."
    )


def test_pdf_blank_line_still_splits_paragraphs():
    text = "PRESS NOTE ON GDP \n \nThe Economy has exhibited sustained performance."
    assert split_sentences(merge_pdf_line_wraps(text)) == [
        "PRESS NOTE ON GDP", "The Economy has exhibited sustained performance.",
    ]


def test_html_single_newlines_between_blocks_still_split():
    """Trafilatura marks every block boundary with one newline and no blank-line
    signal, so merge_pdf_line_wraps must not run on HTML text."""
    text = "Report\nIndia GDP grew 7.1% in FY2024-25, the ministry said.\nFiller text."
    assert split_sentences(text) == [
        "Report", "India GDP grew 7.1% in FY2024-25, the ministry said.", "Filler text.",
    ]


def test_fy_date_forms_tokenize_the_same():
    from components.passage_ranker import tokenize

    compact = tokenize("GDP grew in FY2024-25 overall.")
    spaced = tokenize("GDP grew in FY 2024–25 overall.")
    assert any(t.startswith("fy202425") for t in compact)
    assert any(t.startswith("fy202425") for t in spaced)


def test_percent_forms_tokenize_the_same():
    from components.passage_ranker import tokenize

    assert "5.5%" in tokenize("the repo rate fell to 5.5 per cent.")
    assert "5.5%" in tokenize("the repo rate fell to 5.5%")
    assert "cent" not in tokenize("the rate fell to 5.5 percent")


def test_rbi_style_answer_beats_generic_passages():
    """Live-run regression: RBI wrote '5.5 per cent', and generic passages that
    repeated the bank's name outranked the sentence holding the answer."""
    a_claim = claim(
        "The Reserve Bank of India cut the repo rate to 5.5% in June 2025.",
        ("Reserve Bank of India", "repo rate", "5.5%", "June 2025"),
    )
    filler = [
        f"The Reserve Bank of India and the Reserve Bank announced measure {i} for banks."
        for i in range(120)
    ]
    answer = "The MPC cut the repo rate by 50 bps to 5.5 per cent in June 2025."
    doc = document([*filler[:60], answer, *filler[60:]], doc_id="rbi", ref="doc_07")
    passages = rank_passages([(doc, frozenset({"support"}))], a_claim)
    answer_id = next(s.id for s in doc.sentences if s.text == answer)
    assert any(answer_id in p.sentence_ids for p in passages)


def test_template_queries_keep_the_topic():
    from components.query_planner import template_queries

    a_claim = claim(
        "RBI cut the repo rate to 5.5% in June 2025.", ("RBI", "5.5%", "2025-06"),
        time="2025-06",
    )
    queries = template_queries(a_claim)
    assert all("repo rate" in q for q in queries)
    assert all(q.count("2025-06") == 0 for q in queries)


def test_gap_descriptions_never_leak_internal_labels():
    from components.query_planner import describe_gap

    a_claim = claim("x", ("Myanmar",))
    assert describe_gap("c1_d1", a_claim).endswith("Myanmar")
    for gap in ("primary_source", "independent_sources", "contradiction_search"):
        assert "_" not in describe_gap(gap, a_claim)
        assert gap.replace("_", " ") not in describe_gap(gap, a_claim)


def test_contradicting_sentence_in_a_large_document_still_surfaces():
    """Reproduces a live-run failure: a correct, differently-worded figure for the
    same entity and period was outscored by keyword-dense filler elsewhere in a
    7000+ sentence document, so it never reached the LLM."""
    filler = [
        f"India recorded GDP growth of {9 + i % 3}.{i % 9}% in FY 2025-{26 + i % 3}."
        for i in range(400)
    ]
    correct = "The Economy recorded Real GDP growth of 7.1% during FY 2024–25."
    big_doc = document([*filler[:200], correct, *filler[200:]], doc_id="big", ref="doc_09")
    a_claim = claim("India's GDP grew by 8% in FY2024-25.", ("India", "GDP", "8%", "FY2024-25"))

    passages = rank_passages([(big_doc, frozenset({"support"}))], a_claim)
    correct_id = next(s.id for s in big_doc.sentences if s.text == correct)
    assert any(correct_id in p.sentence_ids for p in passages)


# Validation


def item(sentence_ids, stance="contradicts", values=(), details=("c1_d1",)):
    return EvidenceItem(
        sentence_ids=list(sentence_ids), stance=stance, reason="r",
        addresses_detail_ids=list(details), numeric_values=list(values),
    )


def test_python_builds_quote_from_snapshot():
    doc = document(["India GDP grew 7.1% in FY2024-25."])
    evidence = validate(item(["doc_01_s1"], values=["7.1%"]), claim(), doc)
    assert evidence.text == "India GDP grew 7.1% in FY2024-25."
    assert evidence.validation_status == "valid"


def test_unknown_sentence_id_has_no_document():
    assert validate(item(["doc_99_s1"]), claim(), document()) is None


def test_partly_unknown_ids_are_invalid():
    doc = document(["India GDP data for FY2024-25."])
    evidence = validate(item(["doc_01_s1", "doc_01_s99"]), claim(), doc)
    assert evidence.validation_status == "invalid"


def test_sentence_not_supplied_to_llm_is_invalid():
    doc = document(["India GDP in FY2024-25.", "Other."])
    passage = Passage(
        id="p", document_id="d1", sentence_ids=["doc_01_s1"], text="", bm25_score=0
    )
    evidence = validate_item(item(["doc_01_s2"]), claim(), [passage], {"d1": doc})
    assert evidence.validation_reason == "sentence_not_supplied"
    assert evidence.validation_status == "invalid"


def test_unknown_detail_ids_are_dropped():
    doc = document(["India GDP grew 8% in FY2024-25."])
    evidence = validate(
        item(["doc_01_s1"], stance="supports", values=["8%"], details=("c1_d1", "zz")),
        claim(), doc,
    )
    assert evidence.addresses_detail_ids == ["c1_d1"]


def test_number_not_in_quote_is_ignored():
    doc = document(["India GDP grew in FY2024-25."])
    evidence = validate(item(["doc_01_s1"], stance="supports", values=["8%"]), claim(), doc)
    assert evidence.numbers == []


def test_quarterly_figure_for_annual_claim_is_contextual():
    doc = document(["India GDP grew 8.2% in the third quarter of FY2024-25."])
    evidence = validate(item(["doc_01_s1"], stance="supports", values=["8.2%"]), claim(), doc)
    assert evidence.validation_status == "contextual"
    assert evidence.validation_reason == "time_differs"


def test_supporting_passage_with_wrong_number_is_contextual():
    doc = document(["India GDP grew 7.1% in FY2024-25."])
    evidence = validate(item(["doc_01_s1"], stance="supports", values=["7.1%"]), claim(), doc)
    assert evidence.validation_reason == "number_mismatch"


def test_rounded_match_is_recorded():
    doc = document(["India GDP grew 7.6% in FY2024-25."])
    evidence = validate(item(["doc_01_s1"], stance="supports", values=["7.6%"]), claim(), doc)
    assert evidence.validation_status == "valid"
    assert evidence.number_match == "rounded"


def test_universal_claim_with_limited_source_is_contextual():
    a_claim = claim("All undergraduates get the grant.", ("grant", "undergraduates"))
    doc = document(["The grant is only for undergraduates from low-income families."])
    evidence = validate(item(["doc_01_s1"], stance="supports"), a_claim, doc)
    assert evidence.validation_reason == "scope_differs"


def test_comparison_year_does_not_reject_evidence():
    """Live-run regression: a passage naming 2019 as a comparison was rejected."""
    a_claim = claim("Modi won Varanasi in the 2024 election.", ("Modi", "Varanasi"))
    doc = document(
        ["Modi's margin in Varanasi shrank from his 2019 figure.", "The 2024 results came in."],
    )
    item_ = item(["doc_01_s1"], stance="supports")
    assert validate(item_, a_claim, doc).validation_status == "valid"


def test_document_about_another_year_is_contextual():
    a_claim = claim("Modi won Varanasi in the 2024 election.", ("Modi", "Varanasi"))
    doc = document(["Modi won Varanasi in 2019."])
    item_ = item(["doc_01_s1"], stance="supports")
    assert validate(item_, a_claim, doc).validation_reason == "time_differs"


def test_financial_year_counts_as_the_claim_year():
    doc = document(["India GDP grew 7.1% in 2023."])
    evidence = validate(item(["doc_01_s1"], values=["7.1%"]), claim(), doc)
    assert evidence.validation_reason == "time_differs"


def test_entity_name_with_a_digit_is_still_an_entity():
    """Live-run regression: 'Chandrayaan-3' was skipped for containing a digit, so
    an ISRO source was rejected on 'Moon's south pole' wording alone."""
    a_claim = claim("Chandrayaan-3 landed.", ("Chandrayaan-3", "Moon's south pole"))
    doc = document(["Chandrayaan-3 achieved a soft landing near the lunar south polar region."])
    evidence = validate(item(["doc_01_s1"], stance="supports"), a_claim, doc)
    assert evidence.validation_status == "valid"


def test_mixed_citation_splits_into_one_item_per_document():
    from components.evidence_validator import split_by_document

    parts = split_by_document(item(["doc_01_s5", "doc_02_s2", "doc_01_s6"]))
    assert [p.sentence_ids for p in parts] == [["doc_01_s5", "doc_01_s6"], ["doc_02_s2"]]
    assert split_by_document(item(["doc_01_s5"]))[0].sentence_ids == ["doc_01_s5"]


def test_wrong_entity_is_contextual():
    a_claim = claim("Acme built a bridge.", ("Acme",))
    evidence = validate(item(["doc_01_s1"], stance="supports"), a_claim, document(["Other firm."]))
    assert evidence.validation_reason == "entity_differs"


def test_pubmed_review_is_contextual():
    a_claim = claim("Drug X lowers risk.", ("Drug X",))
    doc = document(
        ["Drug X lowers risk in adults."], domain="pubmed.ncbi.nlm.nih.gov",
        title="Drug X: a systematic review",
    )
    evidence = validate(item(["doc_01_s1"], stance="supports"), a_claim, doc)
    assert evidence.validation_reason == "pubmed_review"


# Sufficiency and independence


def ev(eid="e1", doc="d1", stance="supports", status="valid", details=("c1_d1",), **kw):
    return Evidence(
        id=eid, claim_id="c1", document_id=doc, sentence_ids=["s"], text="t", stance=stance,
        addresses_detail_ids=list(details), reason="r", validation_status=status, **kw,
    )


def test_identical_text_counts_as_one_source():
    docs = {
        "d1": document(doc_id="d1", domain="a.com", content_hash="same"),
        "d2": document(doc_id="d2", domain="b.com", content_hash="same"),
        "d3": document(doc_id="d3", domain="c.com", content_hash="other"),
    }
    items = [ev(f"e{i}", f"d{i}") for i in (1, 2, 3)]
    assert independent_count(items, docs) == 2


def test_social_posts_do_not_count():
    docs = {"d1": document(doc_id="d1", domain="x.com")}
    assert independent_count([ev()], docs) == 0


def test_sufficiency_uses_detail_ids():
    a_claim = claim("x", ("a", "b"))
    rules = CONFIG.rules["official_record"]
    docs = {"d1": document()}
    result = assess_sufficiency(
        a_claim, rules, [ev(details=("c1_d1",))], docs, {"d1": "primary"}, True, False
    )
    assert result.action == "investigate"
    assert result.gaps == ["c1_d2"]


def test_sufficiency_stops_after_gap_round():
    a_claim = claim("x", ("a",))
    result = assess_sufficiency(a_claim, CONFIG.rules["official_record"], [], {}, {}, False, True)
    assert result.action == "stop"
    assert set(result.gaps) == {"c1_d1", "independent_sources", "contradiction_search"}


# Budget


def test_retries_cannot_consume_mandatory_budget():
    budget = AttemptBudget(limit=4)
    budget.plan_mandatory(4)
    assert budget.start_mandatory()
    assert not budget.take_extra()


def test_gap_searches_use_spare_budget():
    budget = AttemptBudget(limit=18)
    budget.plan_mandatory(12)
    assert budget.take_extra()


# Verdicts


def verdict_input(evidence, rule="official_record", tiers=None, details=("a",), **kw):
    docs = {e.document_id: document(doc_id=e.document_id, domain=f"{e.document_id}.com",
                                    content_hash=e.document_id) for e in evidence}
    base = dict(
        claim=claim("x", details), rules=CONFIG.rules[rule], evidence=evidence, documents=docs,
        tiers=tiers or {}, gaps=[], policy_version="1",
    )
    return VerdictInput(**{**base, **kw})


def test_r0_error_has_no_confidence():
    result = decide(verdict_input([], analysis_failed=True))
    assert (result.verdict, result.confidence, result.rule_id) == ("error", None, "R0_ERROR")


def test_r1_conflict():
    items = [ev("e1", "d1"), ev("e2", "d2", stance="contradicts")]
    result = decide(verdict_input(items, tiers={"d1": "primary", "d2": "primary"}))
    assert result.verdict == "unverified_conflicting"


def test_r2_contradicted():
    items = [ev("e1", "d1", stance="contradicts", number_match=None)]
    result = decide(verdict_input(items, tiers={"d1": "primary"}))
    assert result.verdict == "contradicted"
    assert items[0].used_in_verdict


def limited(eid="e2", doc="d2", reason="scope_differs"):
    return ev(eid, doc, status="contextual", validation_reason=reason)


def test_contradiction_beats_misleading():
    items = [ev("e1", "d1", stance="contradicts"), limited()]
    tiers = {"d1": "primary", "d2": "primary"}
    assert decide(verdict_input(items, tiers=tiers)).rule_id == "R2_CONTRADICTED"


def test_misleading_needs_a_primary_source():
    assert decide(verdict_input([limited()])).rule_id == "R5_INSUFFICIENT"
    result = decide(verdict_input([limited()], tiers={"d2": "primary"}))
    assert result.rule_id == "R3_MISLEADING"


def test_quarterly_figure_from_primary_source_is_misleading():
    items = [limited(reason="time_differs")]
    assert decide(verdict_input(items, tiers={"d2": "primary"})).verdict == "misleading"


def test_other_contextual_reasons_are_not_misleading():
    items = [limited(reason="number_mismatch")]
    assert decide(verdict_input(items, tiers={"d2": "primary"})).rule_id == "R5_INSUFFICIENT"


def test_direct_primary_support_outranks_a_limiting_passage():
    items = [ev("e1", "d1", number_match="exact"), limited()]
    tiers = {"d1": "primary", "d2": "primary"}
    assert decide(verdict_input(items, tiers=tiers)).verdict == "supported"


def test_r4_supported_high_with_primary():
    items = [ev(number_match="exact")]
    result = decide(verdict_input(items, tiers={"d1": "primary"}))
    assert (result.verdict, result.confidence) == ("supported", "high")


def test_rounded_match_cannot_get_high_confidence():
    a_claim = claim("x", ("8%",))
    items = [ev(number_match="rounded")]
    result = decide(verdict_input(items, tiers={"d1": "primary"}, claim=a_claim))
    assert (result.verdict, result.confidence) == ("supported", "medium")


def test_official_record_can_be_settled_by_two_publishers_at_medium():
    items = [ev("e1", "d1"), ev("e2", "d2")]
    result = decide(verdict_input(items, rule="official_record"))
    assert (result.verdict, result.confidence) == ("supported", "medium")


def test_one_unofficial_publisher_cannot_settle_any_claim_type():
    for rule in ("official_record", "scientific", "reported_event"):
        result = decide(verdict_input([ev("e1", "d1")], rule=rule))
        assert result.verdict == "unverified_insufficient"


def test_primary_preferred_allows_two_publishers_at_medium():
    items = [ev("e1", "d1"), ev("e2", "d2")]
    result = decide(verdict_input(items, rule="reported_event"))
    assert (result.verdict, result.confidence) == ("supported", "medium")


def test_local_limit_cannot_overrule_strong_support():
    """Live-run regression: a Tamil Nadu page on one local polling phase turned
    a true national claim into Misleading."""
    items = [ev("e1", "d1"), ev("e2", "d2"), limited("e3", "d3")]
    result = decide(verdict_input(items, rule="reported_event"))
    assert result.verdict == "supported"


def test_one_publisher_is_not_enough_for_event():
    assert decide(verdict_input([ev()], rule="reported_event")).rule_id == "R5_INSUFFICIENT"


def test_weak_contradiction_blocks_supported():
    items = [ev("e1", "d1"), ev("e2", "d2", stance="contradicts")]
    result = decide(verdict_input(items, tiers={"d1": "primary"}))
    assert result.verdict == "unverified_insufficient"


def test_skeptic_failure_caps_confidence():
    items = [ev(number_match="exact")]
    result = decide(verdict_input(items, tiers={"d1": "primary"}, skeptic_failed=True))
    assert result.confidence == "medium"


def test_two_flags_lower_confidence():
    items = [ev("e1", "d1", skeptic_status="flagged"), ev("e2", "d1", skeptic_status="flagged")]
    assert decide(verdict_input(items, tiers={"d1": "primary"})).confidence == "low"


# Report


def verdict(value):
    return ClaimVerdict(
        claim_id="c", verdict=value, confidence=None, rule_id="r",
        policy_version="1", explanation="e",
    )


@pytest.mark.parametrize("values,expected", [
    (["supported", "error"], "assessment_incomplete"),
    (["supported", "supported"], "supported"),
    (["contradicted", "contradicted"], "contradicted"),
    (["supported", "contradicted"], "partially_supported"),
    (["misleading", "unverified_insufficient"], "misleading"),
    (["unverified_insufficient", "unverified_conflicting"], "unverified_conflicting"),
    ([], "no_factual_claims"),
])
def test_overall_result(values, expected):
    assert overall_result([verdict(v) for v in values]) == expected


def test_answer_numbers_must_come_from_quotes_or_the_claim():
    quotes = ["It entered into force on 4 November 2016."]
    claim_text = "It entered into force on 12 December 2015."
    assert sentence_is_grounded("It began on 4 November 2016, not 12 December 2015.", quotes, claim_text)
    assert not sentence_is_grounded("It began on 9 November 2016.", quotes, claim_text)
    assert sentence_is_grounded("It began on 04 November 2016.", quotes, claim_text)
    assert sentence_is_grounded("Growth was Rs 1,200.", ["Growth was Rs 1200."], "x")


def test_answer_rejects_jargon_and_missing_quotes():
    assert not sentence_is_grounded("The ledger says no.", ["a"], "x")
    assert not sentence_is_grounded("No.", [], "x")


@pytest.mark.parametrize("text,expected", [
    ("Growth was 5.4% (S3, S4).", "Growth was 5.4%."),
    ("Growth was 5.4% [S3].", "Growth was 5.4%."),
    ("Growth was 5.4% [L1][L2].", "Growth was 5.4%."),
    ("Growth was 5.4% [1].", "Growth was 5.4%."),
    ("The law passed (2023) after review.", "The law passed (2023) after review."),
])
def test_internal_labels_are_removed_from_answers(text, expected):
    from components.report_writer import BRACKET_LABEL

    assert BRACKET_LABEL.sub("", text) == expected


def test_page_context_limits_and_url_scheme():
    from pydantic import ValidationError

    from schemas.audit import PageContext

    assert PageContext(page_url="https://example.com/a").page_url
    with pytest.raises(ValidationError):
        PageContext(page_url="javascript:alert(1)")


def test_answer_with_a_stray_label_is_rejected():
    assert not sentence_is_grounded("Reported by S3 as growth.", ["Growth."], "x")


def test_cache_keeps_stable_claims_for_good():
    from datetime import datetime, timedelta, timezone

    from services.cache import CURRENT_TTL, EMPTY_TTL, PERMANENT_TTL, RECENT_TTL, ttl_for

    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    stable = claim("ISRO launched a mission in 2023", ("ISRO",))
    assert ttl_for(stable, "ok", now) == PERMANENT_TTL
    assert ttl_for(claim("The current repo rate is 5.5%", ("rate",)), "ok", now) == CURRENT_TTL
    dated = stable.model_copy(update={"time": (now - timedelta(days=2)).date().isoformat()})
    assert ttl_for(dated, "ok", now) == RECENT_TTL
    assert ttl_for(stable, "empty", now) == EMPTY_TTL
    assert ttl_for(stable, "error", now) is None


def test_names_like_aditya_l1_are_not_labels():
    quotes = ["Aditya-L1 was launched on September 2, 2023."]
    assert sentence_is_grounded("Aditya-L1 was launched on 2 September 2023.", quotes, "x")
    assert sentence_is_grounded("Aditya\u2011L1 was launched on 2 September 2023.", quotes, "x")
    assert not sentence_is_grounded("Reported by L1 in 2023.", quotes, "x")


@pytest.mark.parametrize("text,vague", [
    ("The government is giving free laptops to students.", False),
    ("The Centre will raise the limit.", False),
    ("The RBI is cutting rates.", False),
    ("The mission was launched by NASA.", True),
    ("The photo claimed that the minister left.", True),
    ("It rose 8%.", True),
    ("NASA launched the mission.", True),
    ("A misleading photo circulated on social media.", True),
    ("NASA launched the Artemis mission.", False),
    ("ISRO launched Aditya-L1 in 2023.", False),
])
def test_vague_subjects_are_caught_without_rejecting_clear_ones(text, vague):
    item = ExtractedClaim(text=text, source_text=text, kind="factual")
    assert bool(validation_error(item, text)) is vague


@pytest.mark.asyncio
async def test_slow_search_is_cut_off(monkeypatch):
    import asyncio

    import httpx

    import adapters.tavily as tavily
    from schemas.search import SearchRequest

    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200, json={"results": []})

    monkeypatch.setattr(tavily, "SEARCH_TIMEOUT_SECONDS", 0.01)
    adapter = tavily.TavilyAdapter("key", transport=httpx.MockTransport(slow))
    request = SearchRequest(claim_id="c", purpose="support", scope="open", query="q")
    result = await adapter.search(request)
    await adapter.close()
    assert (result.status, result.retryable) == ("error", True)


def number_case(claim_details, quote, listed):
    from components.evidence_validator import check_numbers
    from schemas.claim import AtomicClaim, KeyDetail
    from schemas.evidence import Evidence

    details = [KeyDetail(id=f"d{i}", text=text) for i, text in enumerate(claim_details)]
    a_claim = AtomicClaim(
        id="c", position=1, text=" ".join(claim_details), source_text="x", kind="factual",
        key_details=details, numbers=[n for d in details for n in parse_numbers(d.text)],
    )
    evidence = Evidence(
        id="e", claim_id="c", document_id="d", sentence_ids=["s"], text=quote, stance="supports",
        addresses_detail_ids=[d.id for d in details], reason="r",
        numbers=[n for value in listed for n in parse_numbers(value)],
    )
    return check_numbers(a_claim, evidence), evidence


def test_figure_the_model_left_out_is_read_from_the_quote():
    reason, evidence = number_case(
        ["free cover of Rs 5 lakh", "citizens aged 70 and above"],
        "Rs 5 lakh cover for citizens aged 70 years and above.", ["Rs 5 lakh"],
    )
    assert reason is None
    assert evidence.number_match == "exact"
    assert evidence.addresses_detail_ids == ["d0", "d1"]


def test_quote_that_omits_one_figure_stays_valid_but_doesnt_confirm_it():
    reason, evidence = number_case(
        ["free cover of Rs 5 lakh", "citizens aged 70 and above"],
        "The scheme gives Rs 5 lakh of cover per family.", ["Rs 5 lakh"],
    )
    assert reason is None
    assert evidence.addresses_detail_ids == ["d0"]


def test_quote_that_omits_the_currency_still_matches():
    reason, _ = number_case(["cover of Rs 5 lakh"], "Cover rises to 5 lakh.", ["5 lakh"])
    assert reason is None


@pytest.mark.parametrize("details,quote,listed,expected", [
    (["cover of Rs 5 lakh"], "Cover is Rs 3 lakh.", ["Rs 3 lakh"], "number_mismatch"),
    (["growth of 8%"], "Growth was 6%.", [], "number_mismatch"),
    (["growth of 8%"], "Growth rose 8 percentage points.", ["8 percentage points"], "different_unit"),
    (["aged 70"], "Section 5.2 covers citizens.", [], None),
])
def test_real_number_conflicts_are_still_caught(details, quote, listed, expected):
    assert number_case(details, quote, listed)[0] == expected


def test_bare_number_only_clashes_with_a_similar_figure():
    assert number_case(["aged 70"], "A cover of 5 lakh.", ["5 lakh"])[0] is None
    assert number_case(["aged 70"], "Citizens aged 65 qualify.", ["65"])[0] == "number_mismatch"
