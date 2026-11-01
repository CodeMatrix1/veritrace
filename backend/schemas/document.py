from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass
class Sentence:
    id: str  # Example: doc_01_s42
    text: str


@dataclass
class Document:
    id: str
    ref: str  # Example: doc_01
    audit_id: str
    canonical_url: str
    domain: str
    title: str | None
    content_type: Literal["html", "pdf"]
    content_hash: str
    text_snapshot: str
    sentences: list[Sentence]
    language: str | None
    published_at: datetime | None
    published_raw: str | None
    retrieved_at: datetime
    truncated: bool = False
    retrieved_via: str = "direct"


@dataclass
class Passage:  # In memory only
    id: str
    document_id: str
    sentence_ids: list[str]
    text: str
    bm25_score: float
    origins: frozenset[str] = frozenset()
