import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from adapters.base import SearchAdapter
from components.candidate_selector import build_candidates, select_candidates
from components.claim_classifier import classify_claims as run_classifier
from components.claim_extractor import extract_claims as run_extraction
from components.document_fetcher import (
    DocumentFetcher,
    DocumentStore,
    clearly_unrelated,
)
from components.evidence_analyzer import analyze_evidence
from components.evidence_requirements import requirements_for
from components.evidence_validator import split_by_document, validate_item
from components.passage_ranker import rank_passages
from components.query_planner import plan_gap_searches, plan_initial_searches
from components.report_writer import (
    SUMMARY_LABEL,
    build_ledger,
    evidence_view,
    overall_result,
    write_answer,
)
from components.search_runner import SearchOutcome, SearchRunner
from components.skeptic_reviewer import review_evidence
from components.source_history import build_source_history as make_source_history
from components.source_router import source_tier
from components.sufficiency_checker import assess_sufficiency
from components.text_extractor import ExtractionError, extract_page
from config.loader import AppConfig, EvidenceRule
from config.settings import Settings
from pipeline.verdict_engine import VerdictInput, decide
from prompts import (
    CLASSIFICATION_PROMPT_VERSION,
    EVIDENCE_PROMPT_VERSION,
    EXTRACTION_PROMPT_VERSION,
    QUERY_PROMPT_VERSION,
    SKEPTIC_PROMPT_VERSION,
    SUMMARY_PROMPT_VERSION,
    SYSTEM_RULES,
)
from schemas.claim import AtomicClaim, ClaimClassification
from schemas.document import Document
from schemas.evidence import Evidence
from schemas.search import Candidate, SearchRequest
from schemas.verdict import ClaimVerdict
from services.db import Repository
from services.limits import AttemptBudget
from services.llm import LLMError, LLMService
from services.trace import Tracer
from services.url_safety import FetchError, SafeFetcher, UnsafeURL

logger = logging.getLogger(__name__)

SUCCESSFUL_SEARCH = ("ok", "empty")
AI_UNAVAILABLE = "The AI service is busy or at its daily limit. Please try again later."
URL_FAILURE_MESSAGE = "We could not read that URL. Please paste the text instead."
EXTRACTION_FAILURE_MESSAGE = "We could not extract claims from this input. Please try again."
GENERIC_FAILURE_MESSAGE = "The audit could not be completed. Please submit it again."
PLAIN_GAPS = {
    "primary_source": "No official or original source was found",
    "independent_sources": "Too few separate publishers reported this",
    "contradiction_search": "The search for opposing evidence did not complete",
}
RELATIVE_TIME = re.compile(
    r"\b(today|tonight|yesterday|tomorrow|now|currently|recently|latest|this|last|next|"
    r"ago|current)\b",
    re.IGNORECASE,
)


class AuditCancelled(Exception):
    pass


class PublicError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.public_message = message


@dataclass
class Deps:
    repo: Repository
    llm: LLMService
    runner: SearchRunner
    documents: DocumentFetcher
    fetcher: SafeFetcher
    tracer: Tracer
    config: AppConfig
    settings: Settings
    fact_check: SearchAdapter | None = None


@dataclass
class ClaimState:
    claim: AtomicClaim
    classification: ClaimClassification
    rules: EvidenceRule
    documents: dict[str, Document] = field(default_factory=dict)
    tiers: dict[str, str] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    coverage_gaps: list[str] = field(default_factory=list)
    search_outcomes: list[SearchOutcome] = field(default_factory=list)
    analysis_failed: bool = False
    skeptic_failed: bool = False


@dataclass
class AuditState:
    audit_id: str
    input_type: str
    input_content: str
    force_fresh: bool = False
    context: dict[str, Any] | None = None
    text: str = ""
    extraction_key: str = ""
    claims: list[AtomicClaim] = field(default_factory=list)
    skipped: list[AtomicClaim] = field(default_factory=list)
    classifications: dict[str, ClaimClassification] = field(default_factory=dict)
    claim_states: dict[str, ClaimState] = field(default_factory=dict)
    verdicts: dict[str, ClaimVerdict] = field(default_factory=dict)
    source_history: dict[str, Any] = field(default_factory=dict)
    budget: AttemptBudget = field(default_factory=AttemptBudget)
    store: DocumentStore | None = None


async def extract_text(state: AuditState, deps: Deps) -> AuditState:
    if state.input_type == "text":
        state.text = state.input_content.strip()
        return state
    try:
        page = await deps.fetcher.fetch(state.input_content.strip())
        state.text = (await asyncio.to_thread(extract_page, page)).text
    except (UnsafeURL, FetchError, ExtractionError) as exc:
        raise PublicError(URL_FAILURE_MESSAGE) from exc
    return state


async def stop_if_cancelled(state: AuditState, deps: Deps) -> None:
    if await deps.repo.audit_cancelled(state.audit_id):
        raise AuditCancelled


def source_page(state: AuditState) -> dict[str, str] | None:
    url = (state.context or {}).get("page_url")
    return {"title": state.context.get("page_title") or url, "url": url} if url else None


def report_key(state: AuditState, deps: Deps) -> str:
    """Identify a report by its input and everything that can change its result.

    Text with relative time words only matches on the same day, because "last
    week" or "the current rate" can mean something else tomorrow.
    """
    payload = {
        "text": " ".join(state.text.split()),
        "model": deps.llm.client.model,
        "system_rules": SYSTEM_RULES,
        "prompts": [
            EXTRACTION_PROMPT_VERSION, CLASSIFICATION_PROMPT_VERSION, QUERY_PROMPT_VERSION,
            EVIDENCE_PROMPT_VERSION, SKEPTIC_PROMPT_VERSION, SUMMARY_PROMPT_VERSION,
        ],
        "policy": deps.settings.policy_version,
        "rules": deps.config.rules_version,
        "sources": deps.config.sources.version,
        "skeptic": deps.settings.enable_skeptic,
    }
    if RELATIVE_TIME.search(state.text):
        payload["date"] = datetime.now(timezone.utc).date().isoformat()
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


async def reuse_saved_report(state: AuditState, deps: Deps) -> bool:
    """Finish this audit from a saved report, with no search or LLM calls."""
    key = report_key(state, deps)
    await deps.repo.set_report_key(state.audit_id, key)
    days = deps.settings.report_reuse_days
    if state.force_fresh or days <= 0:
        return False
    saved = await deps.repo.find_report(key, state.audit_id, days)
    if saved is None:
        return False
    report = {
        **saved["report"],
        "audit_id": state.audit_id,
        "reused": True,
        "source_page": source_page(state),
        "checked_at": saved["report"].get("checked_at") or saved["created_at"].isoformat(),
    }
    await deps.repo.finish_reused_audit(
        state.audit_id, str(saved["id"]), saved["overall_result"], report
    )
    await deps.tracer.emit(
        state.audit_id, "report", "reused",
        f"Reused the saved report checked on {report['checked_at'][:10]}",
    )
    return True


def extraction_key(state: AuditState, deps: Deps) -> str:
    """Identify an extraction by everything that can change its output.

    The date is included because relative dates such as "yesterday" resolve
    against it, so the same words can mean a different claim on another day.
    """
    payload = {
        "text": state.text,
        "date": datetime.now(timezone.utc).date().isoformat(),
        "model": deps.llm.client.model,
        "system_rules": SYSTEM_RULES,
        "extraction_prompt": EXTRACTION_PROMPT_VERSION,
        "classification_prompt": CLASSIFICATION_PROMPT_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


async def extract_claims(state: AuditState, deps: Deps) -> AuditState:
    state.extraction_key = extraction_key(state, deps)
    reused = None
    if not state.force_fresh:
        reused = await deps.repo.find_extraction(state.extraction_key, state.audit_id)
    if reused is not None:
        state.claims, state.skipped, state.classifications = reused
        await deps.repo.save_claims(state.audit_id, state.claims, state.skipped)
        for classification in state.classifications.values():
            await deps.repo.save_classification(classification)
        await deps.repo.set_extraction_key(state.audit_id, state.extraction_key)
        await deps.tracer.emit(
            state.audit_id, "extract_claims", "reused",
            "Reused the saved extraction for this exact input",
        )
        return state

    try:
        result = await run_extraction(
            state.text, datetime.now(timezone.utc).date(), deps.llm, state.audit_id
        )
    except LLMError as exc:
        message = EXTRACTION_FAILURE_MESSAGE
        if str(exc) == "unavailable":
            message = exc.public or AI_UNAVAILABLE
        raise PublicError(message) from exc
    state.claims = result.claims
    state.skipped = result.not_investigated
    await deps.repo.save_claims(state.audit_id, state.claims, state.skipped)
    await deps.tracer.emit(
        state.audit_id, "extract_claims", "info",
        f"Found {len(state.claims) + len(state.skipped)} statements, "
        f"investigating {len(state.claims)}",
    )
    return state


async def classify_claims(state: AuditState, deps: Deps) -> AuditState:
    if state.classifications:
        return state
    if state.claims:
        state.classifications = await run_classifier(state.claims, deps.llm, state.audit_id)
        for classification in state.classifications.values():
            await deps.repo.save_classification(classification)
    # A fallback result must not be pinned for later audits. The key is saved
    # last, so it marks a finished extraction.
    if not any(c.fallback for c in state.classifications.values()):
        await deps.repo.set_extraction_key(state.audit_id, state.extraction_key)
    return state


async def _run_searches(
    requests: list[SearchRequest],
    cs: ClaimState,
    state: AuditState,
    deps: Deps,
) -> list[SearchOutcome]:
    outcomes = await asyncio.gather(*(
        deps.runner.run(r, cs.claim, state.budget, state.audit_id, state.force_fresh)
        for r in requests
    ))
    cs.search_outcomes.extend(outcomes)
    for outcome in outcomes:
        req = outcome.request
        stage = f"{req.purpose}_search"
        await deps.tracer.emit(
            state.audit_id, stage, outcome.status,
            f'{req.scope.capitalize()} search: "{req.query}" returned {len(outcome.hits)} results',
            claim_id=cs.claim.id, scope=req.scope, cached=outcome.cached,
        )
        if outcome.status not in SUCCESSFUL_SEARCH:
            cs.coverage_gaps.append(f"{req.purpose} search failed: {outcome.status}")
    return list(outcomes)


async def _gather_evidence(
    outcomes: list[SearchOutcome],
    cs: ClaimState,
    state: AuditState,
    deps: Deps,
    optional: bool = False,
) -> None:
    """Run Steps 6 to 11 for one set of search outcomes."""
    await stop_if_cancelled(state, deps)
    claim_id = cs.claim.id
    candidates, records = build_candidates(outcomes, cs.claim, deps.config)
    fetched_urls = {d.canonical_url for d in cs.documents.values()}
    candidates = [c for c in candidates if c.hit.canonical_url not in fetched_urls]
    selected, replacements = select_candidates(candidates)
    search_ids = [o.search_id for o in outcomes]
    await deps.repo.set_unique_candidates(search_ids, len(candidates))

    batch = await deps.documents.collect(selected, replacements, cs.claim, state.store)
    await deps.tracer.emit(
        state.audit_id, "fetch", "completed",
        f"Found {sum(len(o.hits) for o in outcomes)} results, "
        f"kept {len(candidates)} candidates, fetched {len(batch.documents)} documents",
        claim_id=claim_id,
    )

    usable: list[tuple[Document, frozenset[str]]] = []
    log: list[tuple[str, str, str]] = [(r.url, r.decision, r.reason) for r in records]
    log += [(entry.url, entry.decision, entry.reason) for entry in batch.log]
    for document, candidate in batch.documents:
        kept = await _accept_document(document, candidate, cs, state, deps, search_ids)
        log.append((document.canonical_url, *kept))
        if kept[0] == "kept":
            usable.append((document, frozenset(candidate.origins)))
    for url, decision, reason in log:
        await deps.tracer.emit(
            state.audit_id, "filter", decision, reason, claim_id=claim_id, url=url
        )

    passages = rank_passages(usable, cs.claim)
    if not passages:
        return
    await stop_if_cancelled(state, deps)
    await deps.tracer.emit(
        state.audit_id, "passages", "completed",
        f"Sent {len(passages)} passages to evidence analysis",
        claim_id=claim_id,
        passages=[
            {"ids": [p.sentence_ids[0], p.sentence_ids[-1]], "score": round(p.bm25_score, 2)}
            for p in passages
        ],
    )
    try:
        output = await analyze_evidence(
            cs.claim, passages, cs.documents, deps.llm, state.audit_id
        )
    except LLMError as exc:
        # A failed extra round must not discard evidence already gathered.
        if optional:
            cs.coverage_gaps.append("Some extra sources could not be reviewed in time")
            return
        cs.analysis_failed = True
        cs.coverage_gaps.append(exc.public or AI_UNAVAILABLE)
        return
    items = [
        validate_item(part, cs.claim, passages, cs.documents)
        for item in output.items
        for part in split_by_document(item)
    ]
    new_evidence = [e for e in items if e is not None]
    cs.evidence.extend(new_evidence)
    await deps.repo.save_evidence(new_evidence)
    counts = {
        status: sum(e.validation_status == status for e in new_evidence)
        for status in ("valid", "contextual", "invalid")
    }
    await deps.tracer.emit(
        state.audit_id, "evidence", "completed",
        f"{counts['valid']} valid, {counts['contextual']} contextual, "
        f"{counts['invalid']} invalid evidence items",
        claim_id=claim_id,
    )


async def _accept_document(
    document: Document,
    candidate: Candidate,
    cs: ClaimState,
    state: AuditState,
    deps: Deps,
    search_ids: list[str],
) -> tuple[str, str]:
    if document.canonical_url not in state.store.by_url:
        state.store.add(document)
        await deps.repo.save_document(document)
    await deps.repo.link_search_results(search_ids, document.canonical_url, document.id)

    if document.language != "en":
        cs.coverage_gaps.append(f"Unsupported language: {document.canonical_url}")
        return "rejected", "unsupported_language"
    if clearly_unrelated(document.text_snapshot, cs.claim):
        return "rejected", "clearly_unrelated"
    cs.documents[document.id] = document
    cs.tiers[document.id] = source_tier(document.domain, deps.config)
    if document.truncated:
        cs.coverage_gaps.append(f"Only part of this long document was read: {document.domain}")
    return "kept", "related"


def _contradiction_ran(cs: ClaimState) -> bool:
    return any(
        o.request.purpose == "contradiction" and o.status in SUCCESSFUL_SEARCH
        for o in cs.search_outcomes
    )


def _mandatory_failed(cs: ClaimState) -> bool:
    return any(
        o.request.mandatory and o.status not in SUCCESSFUL_SEARCH
        for o in cs.search_outcomes
    )


async def investigate_claim(cs: ClaimState, state: AuditState, deps: Deps) -> None:
    now = datetime.now(timezone.utc)
    requests = await plan_initial_searches(
        cs.claim, cs.classification, deps.config, deps.llm, state.audit_id, now
    )
    outcomes = await _run_searches(requests, cs, state, deps)
    if deps.fact_check is not None:
        check = SearchRequest(
            claim_id=cs.claim.id, purpose="support", scope="open",
            query=cs.claim.text, adapter="fact_check",
        )
        outcomes += await _run_searches([check], cs, state, deps)
    await _gather_evidence(outcomes, cs, state, deps)

    if not cs.analysis_failed:
        check = assess_sufficiency(
            cs.claim, cs.rules, cs.evidence, cs.documents, cs.tiers,
            _contradiction_ran(cs), gap_round_used=False,
        )
        if check.action == "investigate":
            await _run_gap_round(check.gaps, cs, state, deps)
            check = assess_sufficiency(
                cs.claim, cs.rules, cs.evidence, cs.documents, cs.tiers,
                _contradiction_ran(cs), gap_round_used=True,
            )
        cs.gaps = check.gaps

    if deps.settings.enable_skeptic and not cs.analysis_failed:
        try:
            await review_evidence(cs.claim, cs.evidence, deps.llm, state.audit_id)
            await deps.tracer.emit(
                state.audit_id, "skeptic", "completed",
                f"{sum(e.skeptic_status != 'unchanged' for e in cs.evidence)} findings",
                claim_id=cs.claim.id,
            )
        except LLMError:
            cs.skeptic_failed = True
            cs.coverage_gaps.append("Skeptic review could not run")

    verdict = decide(VerdictInput(
        claim=cs.claim,
        rules=cs.rules,
        evidence=cs.evidence,
        documents=cs.documents,
        tiers=cs.tiers,
        gaps=cs.gaps,
        policy_version=deps.settings.policy_version,
        analysis_failed=cs.analysis_failed,
        skeptic_failed=cs.skeptic_failed,
        mandatory_search_failed=_mandatory_failed(cs),
    ))
    await deps.repo.save_evidence(cs.evidence)
    await deps.repo.save_verdict(verdict)
    await deps.tracer.emit(
        state.audit_id, "verdict", verdict.verdict, verdict.explanation,
        claim_id=cs.claim.id, rule_id=verdict.rule_id, confidence=verdict.confidence,
    )


async def _run_gap_round(
    gaps: list[str], cs: ClaimState, state: AuditState, deps: Deps
) -> None:
    requests = await plan_gap_searches(
        cs.claim, gaps, cs.classification, deps.config, deps.llm, state.audit_id,
        tried=[outcome.request.query for outcome in cs.search_outcomes],
    )
    await deps.tracer.emit(
        state.audit_id, "gap_round", "started",
        f"{len(gaps)} gaps, {len(requests)} targeted searches planned",
        claim_id=cs.claim.id, gaps=gaps,
    )
    if requests:
        outcomes = await _run_searches(requests, cs, state, deps)
        await _gather_evidence(outcomes, cs, state, deps, optional=True)


async def investigate_claims(state: AuditState, deps: Deps) -> AuditState:
    state.store = DocumentStore(state.audit_id)
    state.budget.plan_mandatory(4 * len(state.claims))
    for claim in state.claims:
        await stop_if_cancelled(state, deps)
        classification = state.classifications[claim.id]
        cs = ClaimState(
            claim=claim,
            classification=classification,
            rules=requirements_for(classification, deps.config),
        )
        state.claim_states[claim.id] = cs
        try:
            await investigate_claim(cs, state, deps)
        except AuditCancelled:
            raise
        except Exception:
            logger.exception("Claim investigation failed for %s", claim.id)
            cs.coverage_gaps.append("The system failed while assessing this claim")
            await deps.repo.save_verdict(decide(VerdictInput(
                claim=claim, rules=cs.rules, evidence=[], documents={}, tiers={},
                gaps=[], policy_version=deps.settings.policy_version,
                analysis_failed=True,
            )))
    state.verdicts = await deps.repo.load_verdicts(state.audit_id)
    return state


async def build_source_history(state: AuditState, deps: Deps) -> AuditState:
    if not deps.settings.enable_source_history:
        return state
    state.source_history = {}
    for claim in state.claims:
        cs = state.claim_states[claim.id]
        used = {e.document_id for e in cs.evidence if e.validation_status != "invalid"}
        documents = [cs.documents[i] for i in used]
        if documents:
            state.source_history[claim.id] = make_source_history(documents)
    return state


async def write_report(state: AuditState, deps: Deps) -> AuditState:
    all_documents = dict(state.store.by_id) if state.store else {}
    evidence = {c: cs.evidence for c, cs in state.claim_states.items()}
    gap_text = _gap_text(state)
    ledger = build_ledger(state.claims, evidence, gap_text, all_documents, state.skipped)
    await deps.repo.save_ledger(state.audit_id, ledger)
    answer = await write_answer(
        state.claims, state.verdicts, evidence, all_documents, deps.llm, state.audit_id
    )
    summary = " ".join(sentence["text"] for sentence in answer) or None

    ordered = [state.verdicts[c.id] for c in state.claims if c.id in state.verdicts]
    result = overall_result(ordered)
    report = {
        "audit_id": state.audit_id,
        "status": "completed",
        "overall_result": result,
        "answer": answer,
        "summary": summary,
        "summary_label": SUMMARY_LABEL if summary else None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "reused": False,
        "source_page": source_page(state),
        "claims": [_claim_view(c, state, gap_text, all_documents) for c in state.claims],
        "not_investigated": [
            {"text": c.text, "kind": c.kind, "source_text": c.source_text}
            for c in state.skipped
        ],
        "ledger": ledger,
        "source_history": state.source_history,
        "coverage_gaps": [g for gaps in gap_text.values() for g in gaps],
    }
    await deps.repo.finish_audit(state.audit_id, result, report)
    return state


def _gap_text(state: AuditState) -> dict[str, list[str]]:
    text: dict[str, list[str]] = {}
    for claim_id, cs in state.claim_states.items():
        details = {d.id: d.text for d in cs.claim.key_details}
        described = [
            f"No source confirmed this detail: {details[g]}" if g in details
            else PLAIN_GAPS.get(g, "Some evidence could not be found")
            for g in cs.gaps
        ]
        text[claim_id] = list(dict.fromkeys([*described, *cs.coverage_gaps]))
    return text


def _claim_view(
    claim: AtomicClaim,
    state: AuditState,
    gaps: dict[str, list[str]],
    documents: dict[str, Document],
) -> dict[str, Any]:
    verdict = state.verdicts.get(claim.id)
    cs = state.claim_states.get(claim.id)
    items = cs.evidence if cs else []
    return {
        "id": claim.id,
        "text": claim.text,
        "kind": claim.kind,
        "verdict": verdict.verdict if verdict else None,
        "confidence": verdict.confidence if verdict else None,
        "rule_id": verdict.rule_id if verdict else None,
        "reason_code": verdict.reason_code if verdict else None,
        "explanation": verdict.explanation if verdict else None,
        "evidence": [
            evidence_view(e, documents[e.document_id], cs.tiers.get(e.document_id, "unknown"))
            for e in items if e.validation_status != "invalid"
        ],
        "gaps": gaps.get(claim.id, []),
    }


STAGES = [
    extract_text,
    extract_claims,
    classify_claims,
    investigate_claims,
    build_source_history,
    write_report,
]


async def run_audit(audit_id: str, deps: Deps) -> None:
    row = await deps.repo.get_audit(audit_id)
    state = AuditState(
        audit_id, row["input_type"], row["input_content"], row["force_fresh"], row["input_context"]
    )
    if row["status"] == "cancelled":
        return
    await deps.repo.set_status(audit_id, "running")
    try:
        for stage in STAGES:
            await stop_if_cancelled(state, deps)
            await deps.repo.set_stage(audit_id, stage.__name__)
            await deps.tracer.emit(audit_id, stage.__name__, "started")
            state = await stage(state, deps)
            await deps.tracer.emit(audit_id, stage.__name__, "completed")
            if stage is extract_text and await reuse_saved_report(state, deps):
                return
    except AuditCancelled:
        await deps.tracer.emit(audit_id, "audit", "cancelled", "Cancelled by the user")
    except PublicError as exc:
        await deps.repo.fail_audit(audit_id, exc.public_message)
        await deps.tracer.emit(audit_id, "audit", "failed", exc.public_message)
    except Exception:
        logger.exception("Audit %s failed", audit_id)
        await deps.repo.fail_audit(audit_id, GENERIC_FAILURE_MESSAGE)
        await deps.tracer.emit(audit_id, "audit", "failed", GENERIC_FAILURE_MESSAGE)
