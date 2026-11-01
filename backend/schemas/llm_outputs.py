"""Models for LLM responses. The LLM never fills in IDs or Python-owned fields."""
from typing import Literal

from pydantic import BaseModel, Field

from schemas.claim import ClaimKind
from schemas.evidence import Stance


class ExtractedClaim(BaseModel):
    text: str
    source_text: str
    kind: ClaimKind
    key_details: list[str] = Field(default_factory=list)
    time: str | None = None
    location: str | None = None


class ExtractionOutput(BaseModel):
    claims: list[ExtractedClaim]


class ClassificationItem(BaseModel):
    claim_index: int
    primary_domain: str
    secondary_domains: list[str] = Field(default_factory=list)
    claim_type: str


class ClassificationOutput(BaseModel):
    items: list[ClassificationItem]


class InitialQueryOutput(BaseModel):
    support_query: str
    contradiction_query: str


class QueryOutput(BaseModel):
    queries: list[str] = Field(max_length=2)


class EvidenceItem(BaseModel):
    sentence_ids: list[str]
    stance: Stance
    addresses_detail_ids: list[str] = Field(default_factory=list)
    reason: str
    numeric_values: list[str] = Field(default_factory=list)


class EvidenceOutput(BaseModel):
    items: list[EvidenceItem]


class SkepticIssue(BaseModel):
    evidence_id: str
    type: Literal[
        "wrong_entity", "wrong_date", "wrong_location", "wrong_population",
        "scope_mismatch", "broader_statement", "missing_qualification",
        "outdated", "correlation_not_causation", "other",
    ]
    explanation: str
    action: Literal["downgrade", "flag"]


class SkepticOutput(BaseModel):
    issues: list[SkepticIssue] = Field(default_factory=list)


class SummarySentence(BaseModel):
    text: str
    labels: list[str] = Field(default_factory=list)


class SummaryOutput(BaseModel):
    sentences: list[SummarySentence] = Field(default_factory=list)


class RewriteOutput(BaseModel):
    question: str


class ChatSentence(BaseModel):
    text: str
    labels: list[str] = Field(default_factory=list)
    section: str = "Answer"


class ChatOutput(BaseModel):
    in_scope: bool = True
    sentences: list[ChatSentence] = Field(default_factory=list)
