from dataclasses import dataclass

from components.sufficiency_checker import has_authority, has_primary, independent_count
from config.loader import EvidenceRule
from schemas.claim import AtomicClaim
from schemas.document import Document
from schemas.evidence import Evidence
from schemas.verdict import ClaimVerdict

MISLEADING_REASONS = {"time_differs", "scope_differs", "skeptic_missing_qualification"}
EXPLANATIONS = {
    "R0_ERROR": "The system could not finish assessing this claim. This is not a finding about its truth.",
    "R1_CONFLICT": "Strong sources disagree, and dates, definitions or revisions did not resolve it.",
    "R2_CONTRADICTED": "Strong evidence matching this claim's details disproves it.",
    "R3_MISLEADING": "An official source confirms this fact, but for a different period or group than the claim states.",
    "R4_SUPPORTED": "Strong evidence addresses every key detail, with no material contradiction.",
    "R5_INSUFFICIENT": "The evidence found is not enough to assess this claim.",
    "required_primary_unavailable": "This claim needs a primary source, and none was found.",
}


@dataclass
class VerdictInput:
    claim: AtomicClaim
    rules: EvidenceRule
    evidence: list[Evidence]
    documents: dict[str, Document]
    tiers: dict[str, str]
    gaps: list[str]
    policy_version: str
    analysis_failed: bool = False
    skeptic_failed: bool = False
    mandatory_search_failed: bool = False


def strong(side: list[Evidence], data: VerdictInput) -> bool:
    if has_authority(side, data.tiers, data.rules):
        return True
    if data.rules.primary_required:
        return False
    return independent_count(side, data.documents) >= data.rules.minimum_independent_sources


def confidence_for(verdict: str, used: list[Evidence], data: VerdictInput) -> str:
    if verdict == "unverified_insufficient":
        return "low"
    flags = sum(e.skeptic_status == "flagged" for e in data.evidence)
    detail_gaps = [g for g in data.gaps if g != "contradiction_search"]
    if flags >= 2 or len(detail_gaps) >= 2:
        return "low"

    numbers_exact = not data.claim.numbers or verdict != "supported" or all(
        e.number_match == "exact" for e in used if e.stance == "supports"
    )
    if (
        not data.gaps
        and has_primary(used, data.tiers)
        and numbers_exact
        and not flags
        and not data.skeptic_failed
        and not data.mandatory_search_failed
    ):
        return "high"
    return "medium"


def decide(data: VerdictInput) -> ClaimVerdict:
    def build(
        verdict: str,
        rule_id: str,
        used: list[Evidence],
        reason_code: str | None = None,
    ) -> ClaimVerdict:
        for item in used:
            item.used_in_verdict = True
        return ClaimVerdict(
            claim_id=data.claim.id,
            verdict=verdict,
            confidence=None if verdict == "error" else confidence_for(verdict, used, data),
            rule_id=rule_id,
            reason_code=reason_code,
            policy_version=data.policy_version,
            explanation=EXPLANATIONS[reason_code or rule_id],
        )

    if data.analysis_failed:
        return build("error", "R0_ERROR", [])

    valid = [e for e in data.evidence if e.validation_status == "valid"]
    supporting = [e for e in valid if e.stance == "supports"]
    contradicting = [e for e in valid if e.stance == "contradicts"]
    has_strong_support = strong(supporting, data)
    has_strong_contradiction = strong(contradicting, data)

    if has_strong_support and has_strong_contradiction:
        return build("unverified_conflicting", "R1_CONFLICT", supporting + contradicting)
    if has_strong_contradiction:
        return build("contradicted", "R2_CONTRADICTED", contradicting)

    limiting = [
        e for e in data.evidence
        if e.validation_status == "contextual"
        and e.stance == "supports"
        and e.validation_reason in MISLEADING_REASONS
        and data.tiers.get(e.document_id) == "primary"
    ]
    # Direct primary support outranks a passage about another period or group.
    if limiting and not has_primary(supporting, data.tiers):
        return build("misleading", "R3_MISLEADING", limiting)

    details_answered = all(
        any(d.id in e.addresses_detail_ids for e in valid) for d in data.claim.key_details
    )
    if has_strong_support and details_answered and not contradicting:
        return build("supported", "R4_SUPPORTED", supporting)

    reason = None
    if data.rules.primary_required and not has_authority(valid, data.tiers, data.rules):
        reason = "required_primary_unavailable"
    return build("unverified_insufficient", "R5_INSUFFICIENT", [], reason)
