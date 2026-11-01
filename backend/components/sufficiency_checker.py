from dataclasses import dataclass, field
from typing import Literal

from components.source_router import is_social
from config.loader import EvidenceRule
from schemas.claim import AtomicClaim
from schemas.document import Document
from schemas.evidence import Evidence


@dataclass
class Sufficiency:
    action: Literal["stop", "investigate"]
    reason: str
    gaps: list[str] = field(default_factory=list)


def has_primary(evidence: list[Evidence], tiers: dict[str, str]) -> bool:
    return any(tiers.get(e.document_id) == "primary" for e in evidence)


def has_authority(
    evidence: list[Evidence], tiers: dict[str, str], rules: EvidenceRule
) -> bool:
    if has_primary(evidence, tiers):
        return True
    return rules.secondary_sufficient and any(
        tiers.get(e.document_id) == "secondary" for e in evidence
    )


def independent_count(evidence: list[Evidence], documents: dict[str, Document]) -> int:
    """Distinct publishers after detectable duplicates. Not proof of independence."""
    by_hash: dict[str, str] = {}
    for item in evidence:
        document = documents[item.document_id]
        if not is_social(document.domain):
            by_hash.setdefault(document.content_hash, document.domain)
    return len(set(by_hash.values()))


def assess_sufficiency(
    claim: AtomicClaim,
    rules: EvidenceRule,
    evidence: list[Evidence],
    documents: dict[str, Document],
    tiers: dict[str, str],
    contradiction_search_ran: bool,
    gap_round_used: bool,
) -> Sufficiency:
    valid = [e for e in evidence if e.validation_status == "valid"]
    gaps = [
        d.id for d in claim.key_details
        if not any(d.id in e.addresses_detail_ids for e in valid)
    ]
    if rules.primary_required and not has_authority(valid, tiers, rules):
        gaps.append("primary_source")
    if rules.minimum_independent_sources and not has_primary(valid, tiers):
        if independent_count(valid, documents) < rules.minimum_independent_sources:
            gaps.append("independent_sources")
    if not contradiction_search_ran:
        gaps.append("contradiction_search")
    if not gaps:
        return Sufficiency(action="stop", reason="requirements_met")
    if gap_round_used:
        return Sufficiency(action="stop", gaps=gaps, reason="search_limit")
    return Sufficiency(action="investigate", gaps=gaps, reason="missing_evidence")
