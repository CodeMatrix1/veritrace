import asyncio
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime

from adapters.base import SearchAdapter
from config.settings import Settings
from schemas.claim import AtomicClaim
from schemas.search import AdapterResult, RawResult, SearchHit, SearchRequest
from services.cache import SearchCache
from services.db import Repository
from services.limits import AttemptBudget, Limits
from services.url_safety import canonicalize, domain_of

RETRY_DELAY_SECONDS = 1.0


@dataclass
class SearchOutcome:
    request: SearchRequest
    search_id: str
    status: str
    cached: bool
    hits: list[SearchHit]


def parse_published(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


def to_hit(search_id: str, rank: int, result: RawResult) -> SearchHit:
    canonical = canonicalize(result.url)
    return SearchHit(
        search_id=search_id,
        url=result.url,
        canonical_url=canonical,
        domain=domain_of(canonical),
        title=result.title,
        snippet=result.content,
        rank=rank,
        provider_score=result.score,
        published_at=parse_published(result.published_date),
        published_raw=result.published_date,
        raw_content=result.raw_content,
    )


class SearchRunner:
    def __init__(
        self,
        adapters: dict[str, SearchAdapter],
        cache: SearchCache,
        repo: Repository,
        limits: Limits,
        settings: Settings,
        retry_delay: float = RETRY_DELAY_SECONDS,
    ):
        self.adapters = adapters
        self.cache = cache
        self.repo = repo
        self.limits = limits
        self.settings = settings
        self.retry_delay = retry_delay

    async def _call_tavily(
        self, req: SearchRequest, budget: AttemptBudget, audit_id: str
    ) -> tuple[AdapterResult, int]:
        allowed = budget.start_mandatory() if req.mandatory else budget.take_extra()
        if not allowed:
            return AdapterResult(status="budget_exhausted"), 0

        attempts = 0
        while True:
            attempts += 1
            async with self.limits.tavily:
                result = await self.adapters["tavily"].search(req)
            await self.repo.record_usage(
                audit_id, "tavily", f"search_{req.purpose}", result.status,
                credits_used=(
                    result.credits
                    if result.credits is not None
                    else self.settings.tavily_credits_per_search
                ),
            )
            if not result.retryable or attempts >= 2 or not budget.take_extra():
                return result, attempts
            await asyncio.sleep(self.retry_delay)

    async def run(
        self,
        req: SearchRequest,
        claim: AtomicClaim,
        budget: AttemptBudget,
        audit_id: str,
        fresh: bool = False,
    ) -> SearchOutcome:
        cached = None if fresh else await self.cache.get(req)
        attempts = 0
        if cached is not None:
            result = cached[0]
            if req.mandatory and req.adapter == "tavily":
                budget.skip_mandatory()
        elif req.adapter == "tavily":
            result, attempts = await self._call_tavily(req, budget, audit_id)
            await self.cache.put(req, claim, result)
        else:
            result = await self.adapters[req.adapter].search(req)
            attempts = 1
            await self.repo.record_usage(
                audit_id, req.adapter, f"search_{req.purpose}", result.status
            )
            await self.cache.put(req, claim, result)

        search_id = await self.repo.save_search(
            req, result.status, attempts, cached is not None, len(result.results)
        )
        hits = [to_hit(search_id, i, r) for i, r in enumerate(result.results, start=1)]
        await self.repo.save_search_results(hits)
        return SearchOutcome(req, search_id, result.status, cached is not None, hits)
