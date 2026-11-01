from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

Domain = Literal[
    "government_policy", "elections", "health", "science",
    "finance", "disaster", "weather", "general_news",
]
ClaimType = Literal["official_record", "scientific", "reported_event"]
ClaimKind = Literal["factual", "not_checkable"]


class NumericFact(BaseModel):
    value: Decimal
    unit: str | None = None
    precision: int
    comparator: Literal["exact", "approx", "over", "under"]
    original_text: str


class KeyDetail(BaseModel):
    id: str  # Example: c1_d2
    text: str


class AtomicClaim(BaseModel):
    id: str
    position: int
    text: str
    source_text: str
    kind: ClaimKind
    key_details: list[KeyDetail] = Field(default_factory=list)
    numbers: list[NumericFact] = Field(default_factory=list)
    time: str | None = None
    location: str | None = None


class ClaimClassification(BaseModel):
    claim_id: str
    primary_domain: Domain
    secondary_domains: list[Domain] = Field(default_factory=list, max_length=2)
    claim_type: ClaimType
    fallback: bool = False  # The classifier failed, so don't reuse this result

    @property
    def domains(self) -> set[str]:
        return {self.primary_domain, *self.secondary_domains}


@dataclass
class ExtractionResult:
    claims: list[AtomicClaim]
    not_investigated: list[AtomicClaim]
