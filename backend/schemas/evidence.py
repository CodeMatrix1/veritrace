from typing import Literal

from pydantic import BaseModel, Field

from schemas.claim import NumericFact

Stance = Literal["supports", "contradicts", "irrelevant"]


class Evidence(BaseModel):
    id: str
    claim_id: str
    document_id: str
    sentence_ids: list[str]
    text: str
    stance: Stance
    addresses_detail_ids: list[str] = Field(default_factory=list)
    numbers: list[NumericFact] = Field(default_factory=list)
    reason: str
    number_match: Literal["exact", "rounded"] | None = None
    validation_status: Literal["pending", "valid", "contextual", "invalid"] = "pending"
    validation_reason: str | None = None
    skeptic_status: Literal["unchanged", "downgraded", "flagged"] = "unchanged"
    skeptic_note: str | None = None
    used_in_verdict: bool = False
