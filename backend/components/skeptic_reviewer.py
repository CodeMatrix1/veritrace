from prompts import SKEPTIC_PROMPT, SKEPTIC_PROMPT_VERSION
from schemas.claim import AtomicClaim
from schemas.evidence import Evidence
from schemas.llm_outputs import SkepticOutput
from services.llm import LLMService


async def review_evidence(
    claim: AtomicClaim, evidence: list[Evidence], llm: LLMService, audit_id: str
) -> None:
    """Apply skeptic findings in place. It can only lower evidence status."""
    reviewable = [e for e in evidence if e.validation_status == "valid"]
    if not reviewable:
        return
    labels = {f"e{i}": item for i, item in enumerate(reviewable, start=1)}
    ledger = "\n".join(
        f"{label} [{item.stance}]: {item.text}" for label, item in labels.items()
    )
    output = await llm.generate_structured(
        SKEPTIC_PROMPT.format(claim=claim.text, ledger=ledger),
        SkepticOutput,
        "skeptic_review",
        audit_id,
        SKEPTIC_PROMPT_VERSION,
    )
    for issue in output.issues:
        item = labels.get(issue.evidence_id)
        if item is None:
            continue
        item.skeptic_note = issue.explanation
        # "other" is a catch-all for problems the fixed categories don't name. It is
        # too vague to justify removing evidence from the verdict on its own, and a
        # weak model has used it to downgrade evidence for merely disagreeing with
        # the claim, which is not a defect. Treat it as a flag, never a downgrade.
        if issue.action == "downgrade" and issue.type != "other":
            item.skeptic_status = "downgraded"
            item.validation_status = "contextual"
            item.validation_reason = f"skeptic_{issue.type}"
        else:
            item.skeptic_status = "flagged"
