import asyncio
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from components.chat import answer_question
from config.settings import CHAT_SEARCH_DAILY_LIMIT, PER_VISITOR_HOURLY_LIMIT
from schemas.audit import AuditCreated, AuditInput, AuditResponse, ChatRequest, EventsResponse
from services.llm import LLMError

router = APIRouter(prefix="/api")
budget_lock = asyncio.Lock()
SECONDS_PER_CLAIM = 90.0


class QuotaExceeded(Exception):
    pass


visitors: dict[str, deque[float]] = defaultdict(deque)


def check_visitor(request: Request) -> None:
    """Stop one address using the whole shared limit. Hosts put the real address first."""
    forwarded = request.headers.get("x-forwarded-for", "")
    address = forwarded.split(",")[0].strip() or (request.client.host if request.client else "")
    now = time.monotonic()
    recent = visitors[address]
    while recent and now - recent[0] > 3600:
        recent.popleft()
    if len(recent) >= PER_VISITOR_HOURLY_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")
    recent.append(now)


async def accept_audit(audit_input: AuditInput, deps) -> str:
    settings = deps.settings
    async with budget_lock:
        # Running checks only count once they finish, so include them here.
        active = await deps.repo.count_active_audits()
        if await deps.repo.count_audits_today() + active >= settings.daily_audit_limit:
            raise QuotaExceeded("The daily audit limit has been reached.")
        used = await deps.repo.credits_used_this_month("tavily")
        available = (
            settings.tavily_monthly_credits
            - used
            - settings.tavily_credit_reserve
            - active * settings.max_credits_per_audit
        )
        if available < settings.max_credits_per_audit:
            raise QuotaExceeded("Search credits are too low to start a new audit.")
        return await deps.repo.create_audit(audit_input, settings.policy_version)


def source_page(context: dict | None) -> dict | None:
    """Return only the page details, as older audits also stored nearby text."""
    if not context or not context.get("page_url"):
        return None
    return {"title": context.get("page_title") or context["page_url"], "url": context["page_url"]}


@router.post("/audits", status_code=202, response_model=AuditCreated)
async def create_audit(audit_input: AuditInput, request: Request) -> AuditCreated:
    check_visitor(request)
    deps = request.app.state.deps
    try:
        audit_id = await accept_audit(audit_input, deps)
    except QuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    await request.app.state.queue.put(audit_id)
    return AuditCreated(audit_id=audit_id, status="queued")


@router.get("/audits/{audit_id}", response_model=AuditResponse)
async def get_audit(audit_id: UUID, request: Request) -> AuditResponse:
    repo = request.app.state.deps.repo
    row = await repo.get_audit(str(audit_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Audit not found.")
    finished = row["status"] in ("completed", "failed", "cancelled")
    end = row["updated_at"] if finished else datetime.now(timezone.utc)
    claims = await repo.claim_texts(str(audit_id))
    return AuditResponse(
        audit_id=str(row["id"]),
        status=row["status"],
        input_type=row["input_type"],
        input_text=row["input_content"],
        claims=claims,
        source_page=source_page(row["input_context"]),
        created_at=row["created_at"],
        elapsed_seconds=(end - row["created_at"]).total_seconds(),
        # The count is known once claims are found, so the estimate starts at one claim.
        estimate_seconds=SECONDS_PER_CLAIM * max(len(claims), 1),
        current_stage=row["current_stage"],
        overall_result=row["overall_result"],
        public_error=row["public_error"],
        report=row["report"],
    )


@router.post("/audits/{audit_id}/cancel")
async def cancel_audit(audit_id: UUID, request: Request) -> dict:
    repo = request.app.state.deps.repo
    if await repo.get_audit(str(audit_id)) is None:
        raise HTTPException(status_code=404, detail="Audit not found.")
    if not await repo.cancel_audit(str(audit_id)):
        raise HTTPException(status_code=409, detail="This audit has already finished.")
    return {"status": "cancelled"}


@router.get("/audits/{audit_id}/events", response_model=EventsResponse)
async def get_events(audit_id: UUID, request: Request, after_id: int = 0) -> EventsResponse:
    repo = request.app.state.deps.repo
    # Read status first, so a finished audit cannot hide its last events.
    finished = await repo.audit_finished(str(audit_id))
    rows = await repo.trace_after(str(audit_id), after_id)
    return EventsResponse(
        events=rows,
        last_id=rows[-1]["id"] if rows else after_id,
        finished=finished,
    )


@router.post("/chat")
async def chat(body: ChatRequest, request: Request) -> dict:
    check_visitor(request)
    deps = request.app.state.deps
    if await deps.repo.count_audits_today() >= deps.settings.daily_audit_limit:
        raise HTTPException(status_code=429, detail="The daily audit limit has been reached.")
    try:
        searches_left = await deps.repo.count_chat_searches_today() < CHAT_SEARCH_DAILY_LIMIT
        reply = await answer_question(
            body.question, deps.repo, deps.llm,
            search=deps.runner.adapters.get("tavily") if searches_left else None,
            fetcher=deps.fetcher,
            document_url=body.document_url,
            blocked=list(deps.config.sources.blocked),
            history=[turn.model_dump() for turn in body.history],
        )
    except LLMError as exc:
        raise HTTPException(
            status_code=503,
            detail=exc.public or "The AI service is busy. Please try again later.",
        ) from exc
    if reply["found"]:
        await deps.repo.record_usage(None, "veritrace", "chat_question", "ok")
    return reply


@router.get("/chat/status")
async def chat_status(request: Request) -> dict:
    deps = request.app.state.deps
    used = await deps.repo.count_audits_today()
    return {
        "pages": await deps.repo.count_knowledge_pages(),
        "remaining": max(deps.settings.daily_audit_limit - used, 0),
    }


@router.get("/health")
async def health(request: Request) -> dict:
    deps = request.app.state.deps
    used_today = await deps.repo.count_audits_today()
    return {
        "status": "ok",
        "daily_audits_remaining": max(deps.settings.daily_audit_limit - used_today, 0),
    }
