from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator


class PageContext(BaseModel):
    """Where selected text came from. It is shown on the report, and never read by the AI."""

    page_title: str | None = Field(default=None, max_length=300)
    page_url: str | None = Field(default=None, max_length=2000)  # Shown only, never fetched

    @model_validator(mode="after")
    def check_page_url(self) -> "PageContext":
        if self.page_url:
            parts = urlsplit(self.page_url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ValueError("page_url must be an http or https URL")
        return self


class AuditInput(BaseModel):
    type: Literal["text", "url"]
    content: str = Field(min_length=3, max_length=12000)
    fresh: bool = False  # Search again, even if this text was checked recently
    context: PageContext | None = None

    @model_validator(mode="after")
    def check_url(self) -> "AuditInput":
        if self.type == "url":
            parts = urlsplit(self.content.strip())
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ValueError("content must be an http or https URL")
        return self


class AuditCreated(BaseModel):
    audit_id: str
    status: str


class AuditResponse(BaseModel):
    audit_id: str
    status: str
    input_type: str
    input_text: str
    claims: list[str] = Field(default_factory=list)
    source_page: dict[str, str] | None = None
    created_at: datetime
    elapsed_seconds: float
    estimate_seconds: float
    current_stage: str | None = None
    overall_result: str | None = None
    public_error: str | None = None
    report: dict[str, Any] | None = None


class ChatTurn(BaseModel):
    question: str = Field(max_length=500)
    answer: str = Field(max_length=2000)
    source_urls: list[str] = Field(default_factory=list, max_length=8)


class ChatRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    document_url: str | None = Field(default=None, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def check_document_url(self) -> "ChatRequest":
        if self.document_url:
            parts = urlsplit(self.document_url.strip())
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ValueError("document_url must be an http or https URL")
        return self


class EventsResponse(BaseModel):
    events: list[dict[str, Any]]
    last_id: int
    finished: bool
