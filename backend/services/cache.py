import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from components.source_router import is_social
from schemas.claim import AtomicClaim
from schemas.search import AdapterResult, RawResult, SearchRequest
from services.db import Repository
from services.url_safety import domain_of

RECENT_DAYS = 7
RECENT_TTL = timedelta(hours=1)
CURRENT_TTL = timedelta(hours=24)
PERMANENT_TTL = timedelta(days=36500)
EMPTY_TTL = timedelta(minutes=10)
# The answer to these changes over time, so an old result could be wrong.
CURRENT_WORDS = re.compile(r"\b(today|now|currently|current|latest|recent|recently)\b", re.IGNORECASE)


def cache_key(req: SearchRequest) -> str:
    payload = {
        "provider": req.adapter,
        "query": req.query,
        "depth": "basic",
        "raw_content": True,
        "include": sorted(req.include_domains),
        "exclude": sorted(req.exclude_domains),
        "max_results": req.max_results,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def claim_date(claim: AtomicClaim) -> datetime | None:
    if not claim.time:
        return None
    try:
        parsed = datetime.fromisoformat(claim.time[:10])
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def is_recent(claim: AtomicClaim, now: datetime, days: int = RECENT_DAYS) -> bool:
    date = claim_date(claim)
    return date is not None and now - date <= timedelta(days=days)


def ttl_for(claim: AtomicClaim, status: str, now: datetime) -> timedelta | None:
    if status == "empty":
        return EMPTY_TTL
    if status != "ok":
        return None
    if is_recent(claim, now):
        return RECENT_TTL
    return CURRENT_TTL if CURRENT_WORDS.search(claim.text) else PERMANENT_TTL


class SearchCache:
    def __init__(self, repo: Repository):
        self.repo = repo

    async def get(self, req: SearchRequest) -> tuple[AdapterResult, datetime] | None:
        row = await self.repo.cache_get(cache_key(req))
        if row is None:
            return None
        stored = row["response"]["results"]
        texts = await self.repo.page_texts([r["url"] for r in stored])
        results = [RawResult(**{**r, "raw_content": texts.get(r["url"])}) for r in stored]
        return AdapterResult(status=row["status"], results=results), row["retrieved_at"]

    async def put(
        self, req: SearchRequest, claim: AtomicClaim, result: AdapterResult
    ) -> None:
        now = datetime.now(timezone.utc)
        ttl = ttl_for(claim, result.status, now)
        if ttl is None:
            return
        await self.repo.save_page_texts({
            r.url: r.raw_content
            for r in result.results
            if r.raw_content and not is_social(domain_of(r.url))
        })
        await self.repo.cache_put(
            cache_key(req),
            req.adapter,
            {"results": [r.model_dump(exclude={"raw_content"}) for r in result.results]},
            result.status,
            now,
            now + ttl,
        )
