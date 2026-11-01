from typing import Literal

from pydantic import BaseModel

VerdictValue = Literal[
    "supported", "contradicted", "misleading",
    "unverified_insufficient", "unverified_conflicting", "error",
]
Confidence = Literal["high", "medium", "low"]


class ClaimVerdict(BaseModel):
    claim_id: str
    verdict: VerdictValue
    confidence: Confidence | None
    rule_id: str
    reason_code: str | None = None
    policy_version: str
    explanation: str
