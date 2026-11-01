import logging
import re
from typing import Any

from prompts import SUMMARY_PROMPT, SUMMARY_PROMPT_VERSION
from schemas.claim import AtomicClaim
from schemas.document import Document
from schemas.evidence import Evidence
from schemas.llm_outputs import SummaryOutput
from schemas.verdict import ClaimVerdict
from services.llm import LLMError, LLMService

logger = logging.getLogger(__name__)

SUMMARY_LABEL = "AI-written from the sources listed. Check them before relying on this."
# Parentheses need a letter, so a real value such as "(2023)" is left alone.
BRACKET_LABEL = re.compile(
    r"\s*(?:\[\s*[LS]?\d+(?:\s*,\s*[LS]?\d+)*\s*\]|\(\s*[LS]\d+(?:\s*,\s*[LS]\d+)*\s*\))"
)
NUMBER = re.compile(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?")
# A label stands alone, so a name such as "Aditya-L1" is not treated as one.
JARGON = re.compile(
    r"\b(ledger|stance|model assessment)\b|(?<![\w\-\u2010-\u2015])[LS]\d+\b", re.IGNORECASE
)
RESULT_WORDS = {
    "supported": "Supported",
    "contradicted": "Contradicted",
    "misleading": "Misleading",
    "unverified_conflicting": "Sources disagree",
}
FALLBACK_OPENERS = {
    "supported": "Sources support this claim.",
    "contradicted": "Sources contradict this claim.",
    "misleading": "This fact is real, but it applies to a different period or group.",
    "unverified_conflicting": "Reliable sources disagree about this claim.",
}
NO_EVIDENCE = "There isn't enough reliable evidence to confirm or disprove this claim."
NO_RESULT = "This claim could not be fully checked."
SERIOUSNESS = (
    "contradicted", "misleading", "unverified_conflicting", "unverified_insufficient",
)


def overall_result(verdicts: list[ClaimVerdict]) -> str:
    values = [v.verdict for v in verdicts]
    if not values:
        return "no_factual_claims"
    if "error" in values:
        return "assessment_incomplete"
    if all(v == "supported" for v in values):
        return "supported"
    if all(v == "contradicted" for v in values):
        return "contradicted"
    if "supported" in values:
        return "partially_supported"
    return next(s for s in SERIOUSNESS if s in values)


def build_ledger(
    claims: list[AtomicClaim],
    evidence: dict[str, list[Evidence]],
    gaps: dict[str, list[str]],
    documents: dict[str, Document],
    skipped: list[AtomicClaim],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    def add(claim_id: str | None, kind: str, statement: str, evidence_ids: list[str]) -> None:
        entries.append({
            "label": f"L{len(entries) + 1}",
            "claim_id": claim_id,
            "kind": kind,
            "statement": statement,
            "evidence_ids": evidence_ids,
        })

    for claim in claims:
        for item in evidence.get(claim.id, []):
            if item.validation_status == "invalid":
                continue
            if item.validation_status == "valid" or item.used_in_verdict:
                source = documents[item.document_id]
                add(
                    claim.id, "source_statement",
                    f'{source.domain} states: "{item.text}"', [item.id],
                )
            add(claim.id, "model_assessment", item.reason, [item.id])
            if item.skeptic_note:
                add(claim.id, "model_assessment", item.skeptic_note, [item.id])
        for gap in gaps.get(claim.id, []):
            add(claim.id, "uncertain", gap, [])
    for claim in skipped:
        add(None, "uncertain", f"Not checked, as it can't be proven true or false: {claim.text}", [])
    return entries


def number_tokens(text: str) -> set[str]:
    return {t.replace(",", "").lstrip("0") or "0" for t in NUMBER.findall(text)}


def sentence_is_grounded(text: str, quotes: list[str], claim_text: str) -> bool:
    """Every number must come from the cited quotes, or restate the claim."""
    if not text or not quotes or JARGON.search(text):
        return False
    return number_tokens(text) <= number_tokens(" ".join([*quotes, claim_text]))


def fallback_sentence(
    verdict: ClaimVerdict | None, items: list[tuple[Evidence, Document]]
) -> dict[str, Any]:
    if verdict is None or verdict.verdict == "error":
        return {"text": NO_RESULT, "sources": []}
    if not items:
        return {"text": NO_EVIDENCE, "sources": []}
    item, document = min(items, key=lambda pair: len(pair[0].text))
    opener = FALLBACK_OPENERS[verdict.verdict]
    quote = item.text.replace("*", "")[:200]
    return {
        "text": f'{opener} {document.domain} says: "{quote}"',
        "sources": [{"publisher": document.domain, "url": document.canonical_url}],
    }


async def write_answer(
    claims: list[AtomicClaim],
    verdicts: dict[str, ClaimVerdict],
    evidence: dict[str, list[Evidence]],
    documents: dict[str, Document],
    llm: LLMService,
    audit_id: str,
) -> list[dict[str, Any]]:
    """Write a plain answer from evidence already gathered, with no new searches.

    Labels stay internal. They tie each sentence to its quotes, so Python can reject
    any figure the sources don't state, and readers see source links instead.
    """
    statements: dict[str, tuple[AtomicClaim, Evidence, Document]] = {}
    blocks = []
    for claim in claims:
        verdict = verdicts.get(claim.id)
        used = [e for e in evidence.get(claim.id, []) if e.used_in_verdict]
        if verdict is None or verdict.verdict not in RESULT_WORDS or not used:
            continue
        lines = []
        for item in used:
            label = f"S{len(statements) + 1}"
            document = documents[item.document_id]
            statements[label] = (claim, item, document)
            lines.append(f'[{label}] {document.domain}: "{item.text}"')
        blocks.append(
            f'Claim: {claim.text}\nResult: {RESULT_WORDS[verdict.verdict]}\n' + "\n".join(lines)
        )

    written: dict[str, list[dict[str, Any]]] = {}
    covered: set[str] = set()
    if blocks:
        try:
            output = await llm.generate_structured(
                SUMMARY_PROMPT.format(results="\n\n".join(blocks)),
                SummaryOutput, "summary", audit_id, SUMMARY_PROMPT_VERSION,
            )
        except LLMError:
            output = SummaryOutput()
        for sentence in output.sentences:
            cited = [statements[label] for label in sentence.labels if label in statements]
            text = BRACKET_LABEL.sub("", sentence.text).strip()
            if sentence.labels and not cited:
                logger.warning("Answer sentence cites unknown sources: %s", text)
                continue
            claim_ids = {c[0].id for c in cited} or {s[0].id for s in statements.values()}
            # A summary sentence can cover several claims. Check it against every quote behind
            # them, as the model's labels can be incomplete. A figure from no quote is rejected.
            same_claim = [s for s in statements.values() if s[0].id in claim_ids]
            quotes = [s[1].text for s in same_claim]
            claim_text = " ".join(dict.fromkeys(s[0].text for s in same_claim))
            if not sentence_is_grounded(text, quotes, claim_text):
                logger.warning("Answer sentence rejected: %s", text)
                continue
            sources = {c[2].domain: c[2].canonical_url for c in cited or same_claim}
            claim = same_claim[0][0]
            covered |= claim_ids
            written.setdefault(claim.id, []).append({
                "claim_id": claim.id,
                "text": text,
                "sources": [{"publisher": d, "url": u} for d, u in sources.items()],
            })

    answer = []
    for claim in claims:
        if claim.id in written:
            answer.extend(written[claim.id])
            continue
        if claim.id in covered:
            continue
        used = [
            (e, documents[e.document_id])
            for e in evidence.get(claim.id, []) if e.used_in_verdict
        ]
        sentence = fallback_sentence(verdicts.get(claim.id), used)
        if len(claims) > 1:
            # Fixed wording says "this claim", which needs naming in a combined answer.
            sentence["text"] = f'"{claim.text}": {sentence["text"]}'
        answer.append({"claim_id": claim.id, "fallback": True, **sentence})
    return answer


def evidence_view(item: Evidence, document: Document, tier: str = "unknown") -> dict[str, Any]:
    return {
        "id": item.id,
        "quote": item.text,
        "stance": item.stance,
        "reason": item.reason,
        "validation_status": item.validation_status,
        "validation_reason": item.validation_reason,
        "skeptic_status": item.skeptic_status,
        "skeptic_note": item.skeptic_note,
        "used_in_verdict": item.used_in_verdict,
        "source_title": document.title,
        "source_url": document.canonical_url,
        "publisher": document.domain,
        "source_tier": tier,
        "retrieved_via": document.retrieved_via,
        "published_date": document.published_raw,
        "retrieved_at": document.retrieved_at.isoformat(),
    }
