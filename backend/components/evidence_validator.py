import re
import uuid

from components.candidate_selector import detail_present
from components.number_compare import best_comparison, parse_numbers
from components.source_router import host_matches
from schemas.claim import AtomicClaim, NumericFact
from schemas.document import Document, Passage
from schemas.evidence import Evidence
from schemas.llm_outputs import EvidenceItem

YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
QUARTER = re.compile(r"\b(q[1-4]|quarter|quarterly)\b", re.IGNORECASE)
UNIVERSAL = re.compile(r"\b(all|every|everyone|any)\b", re.IGNORECASE)
LIMITING = re.compile(
    r"\b(only|limited to|restricted to|eligible|eligibility|subject to|provided that|"
    r"income below|families with)\b",
    re.IGNORECASE,
)
HEDGED = re.compile(
    r"\b(likely to|expected to|may|might|could|reportedly|proposed|proposes|draft|planned|"
    r"plans to|set to|considering|rumou?red|sources said)\b",
    re.IGNORECASE,
)
PUBMED_REVIEW = re.compile(
    r"\b(systematic review|meta-analysis|review article|narrative review)\b", re.IGNORECASE
)


def sentence_lookup(document: Document) -> dict[str, str]:
    return {s.id: s.text for s in document.sentences}


def time_mismatch(claim: AtomicClaim, text: str, document: Document) -> bool:
    claim_text = f"{claim.text} {claim.time or ''}"
    claim_years = set(YEAR.findall(claim_text))
    source_years = set(YEAR.findall(text))
    if claim_years and source_years and not claim_years & source_years:
        # Another year in the quote can be a comparison, so it only counts as a
        # mismatch if the document never mentions the claim's year at all.
        document_text = f"{document.title or ''} {document.text_snapshot}"
        if not claim_years & set(YEAR.findall(document_text)):
            return True
    return bool(QUARTER.search(text)) and not QUARTER.search(claim_text)


def scope_mismatch(claim: AtomicClaim, evidence: Evidence) -> bool:
    return (
        evidence.stance == "supports"
        and bool(UNIVERSAL.search(claim.text))
        and bool(LIMITING.search(evidence.text))
    )


def hedged_support(claim: AtomicClaim, evidence: Evidence) -> bool:
    """A source saying something is likely can't confirm that it happened."""
    return (
        evidence.stance == "supports"
        and bool(HEDGED.search(evidence.text))
        and not HEDGED.search(claim.text)
    )


def entity_mismatch(claim: AtomicClaim, document: Document) -> bool:
    names = [d.text for d in claim.key_details if not parse_numbers(d.text)]
    if not names:
        return False
    haystack = f"{document.title or ''} {document.text_snapshot}"
    return not any(detail_present(name, haystack) for name in names)


def location_mismatch(claim: AtomicClaim, document: Document) -> bool:
    if not claim.location:
        return False
    return not detail_present(
        claim.location, f"{document.title or ''} {document.text_snapshot}"
    )


def number_outcome(number: NumericFact, evidence: Evidence) -> str:
    """Compare one claim number with a quote, or return absent if the quote doesn't state it."""
    listed = evidence.numbers
    found = [*listed, *parse_numbers(evidence.text)]
    outcome = best_comparison(number, found) if found else "absent"
    if outcome in ("exact", "match", "rounded"):
        return outcome
    same_value = [fact for fact in found if fact.value == number.value]
    if same_value:
        # "5 lakh" in a quote matches a claim of "Rs 5 lakh", as one side just omits the unit.
        if number.unit is None or any(fact.unit is None for fact in same_value):
            return "exact"
        return "different_unit"
    if number.unit is not None:
        return "mismatch" if any(fact.unit == number.unit for fact in found) else "absent"
    # A bare number can only clash with a listed figure of similar size. An age of 70 says nothing
    # about a section number, or a sum of 5 lakh.
    similar = [f for f in listed if f.unit is None and number.value / 2 <= f.value <= number.value * 2]
    return "mismatch" if similar else "absent"


def check_numbers(claim: AtomicClaim, evidence: Evidence) -> str | None:
    """Apply number rules, returning a reason if the evidence is only contextual."""
    if not claim.numbers or evidence.stance not in ("supports", "contradicts"):
        return None
    outcomes = [number_outcome(n, evidence) for n in claim.numbers]
    # A quote can't confirm a detail whose figure it doesn't state.
    absent = {n.value for n, o in zip(claim.numbers, outcomes) if o == "absent"}
    unconfirmed = {
        d.id for d in claim.key_details if absent & {n.value for n in parse_numbers(d.text)}
    }
    evidence.addresses_detail_ids = [
        i for i in evidence.addresses_detail_ids if i not in unconfirmed
    ]
    stated = [o for o in outcomes if o != "absent"]
    if evidence.stance == "contradicts":
        return "different_unit" if stated and all(o == "different_unit" for o in stated) else None
    if "different_unit" in stated:
        return "different_unit"
    if "mismatch" in stated:
        return "number_mismatch"
    if stated:
        evidence.number_match = "rounded" if "rounded" in stated else "exact"
    return None


def split_by_document(item: EvidenceItem) -> list[EvidenceItem]:
    """Give each document its own item, so a quote never spans two sources."""
    groups: dict[str, list[str]] = {}
    for sentence_id in item.sentence_ids:
        groups.setdefault(sentence_id.rsplit("_s", 1)[0], []).append(sentence_id)
    if len(groups) < 2:
        return [item]
    return [item.model_copy(update={"sentence_ids": ids}) for ids in groups.values()]


def validate_item(
    item: EvidenceItem,
    claim: AtomicClaim,
    passages: list[Passage],
    documents: dict[str, Document],
) -> Evidence | None:
    """Build evidence from one LLM item, or None if it cites no saved document."""
    supplied = {sid: p.document_id for p in passages for sid in p.sentence_ids}
    known_documents = {sid: d for d in documents.values() for sid in sentence_lookup(d)}
    document = next(
        (known_documents[sid] for sid in item.sentence_ids if sid in known_documents), None
    )
    if document is None:
        return None

    lookup = sentence_lookup(document)
    in_document = [sid for sid in item.sentence_ids if sid in lookup]
    detail_ids = {d.id for d in claim.key_details}
    text = " ".join(lookup[sid] for sid in in_document)
    numbers = [
        fact
        for value in item.numeric_values
        for fact in parse_numbers(value)
        if value.strip() and value.strip() in text
    ]
    evidence = Evidence(
        id=str(uuid.uuid4()),
        claim_id=claim.id,
        document_id=document.id,
        sentence_ids=item.sentence_ids,
        text=text,
        stance=item.stance,
        addresses_detail_ids=[i for i in item.addresses_detail_ids if i in detail_ids],
        numbers=numbers,
        reason=item.reason,
    )

    def result(status: str, reason: str | None) -> Evidence:
        evidence.validation_status = status
        evidence.validation_reason = reason
        return evidence

    if len(in_document) != len(item.sentence_ids) or not item.sentence_ids:
        return result("invalid", "unknown_sentence_id")
    if any(sid not in supplied for sid in item.sentence_ids):
        return result("invalid", "sentence_not_supplied")
    if item.stance == "irrelevant":
        return result("invalid", "irrelevant")
    if document.language != "en":
        return result("invalid", "unsupported_language")
    if host_matches(document.domain, "pubmed.ncbi.nlm.nih.gov") and PUBMED_REVIEW.search(
        f"{document.title or ''} {document.text_snapshot[:2000]}"
    ):
        return result("contextual", "pubmed_review")
    if entity_mismatch(claim, document):
        return result("contextual", "entity_differs")
    if location_mismatch(claim, document):
        return result("contextual", "location_differs")
    if time_mismatch(claim, text, document):
        return result("contextual", "time_differs")
    if scope_mismatch(claim, evidence):
        return result("contextual", "scope_differs")
    if hedged_support(claim, evidence):
        return result("contextual", "hedged_source")
    number_reason = check_numbers(claim, evidence)
    if number_reason:
        return result("contextual", number_reason)
    return result("valid", None)
