from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_validator

Purpose = Literal["support", "contradiction", "gap"]
Scope = Literal["official", "open"]
SearchStatus = Literal["ok", "empty", "error", "rate_limited", "budget_exhausted"]


@dataclass
class SearchRequest:
    claim_id: str
    purpose: Purpose
    scope: Scope
    query: str
    include_domains: list[str] = field(default_factory=list)
    exclude_domains: list[str] = field(default_factory=list)
    max_results: int = 8
    adapter: str = "tavily"
    mandatory: bool = False


class RawResult(BaseModel):
    url: str
    title: str = ""
    content: str = ""
    published_date: str | None = None
    score: float | None = None
    raw_content: str | None = None

    @field_validator("title", "content", "raw_content", mode="before")
    @classmethod
    def strip_null_characters(cls, value: str | None) -> str:
        # PostgreSQL cannot store null characters, and web text sometimes has them.
        return (value or "").replace("\x00", "")


class SearchHit(BaseModel):
    search_id: str
    url: str
    canonical_url: str
    domain: str
    title: str
    snippet: str
    rank: int
    provider_score: float | None = None
    published_at: datetime | None = None
    published_raw: str | None = None
    raw_content: str | None = None


@dataclass
class AdapterResult:
    status: SearchStatus
    results: list[RawResult] = field(default_factory=list)
    credits: float | None = None
    retryable: bool = False


@dataclass
class Candidate:
    hit: SearchHit
    tier: str = "unknown"
    score: float = 0.0
    origins: set[str] = field(default_factory=set)
