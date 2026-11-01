import asyncio

import httpx
from pydantic import BaseModel, Field, ValidationError

from schemas.search import AdapterResult, RawResult, SearchRequest

API_URL = "https://api.tavily.com/search"
# httpx only limits gaps between data, so a slow reply needs its own total limit.
SEARCH_TIMEOUT_SECONDS = 20.0


class TavilyResponse(BaseModel):
    results: list[RawResult] = Field(default_factory=list)
    usage: dict | None = None


class TavilyAdapter:
    name = "tavily"

    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None):
        self.client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=20.0),
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def search(self, req: SearchRequest) -> AdapterResult:
        payload = {
            "query": req.query,
            "search_depth": "basic",
            "max_results": req.max_results,
            "include_raw_content": True,
            "include_usage": True,
        }
        if req.include_domains:
            payload["include_domains"] = req.include_domains
        if req.exclude_domains:
            payload["exclude_domains"] = req.exclude_domains

        try:
            response = await asyncio.wait_for(
                self.client.post(API_URL, json=payload), SEARCH_TIMEOUT_SECONDS
            )
        except (httpx.HTTPError, TimeoutError):
            return AdapterResult(status="error", retryable=True)
        if response.status_code == 429:
            return AdapterResult(status="rate_limited", retryable=True)
        if response.status_code >= 500:
            return AdapterResult(status="error", retryable=True)
        if response.status_code != 200:
            return AdapterResult(status="error")

        try:
            parsed = TavilyResponse.model_validate_json(response.content)
        except ValidationError:
            return AdapterResult(status="error")
        credits = (parsed.usage or {}).get("credits")
        return AdapterResult(
            status="ok" if parsed.results else "empty",
            results=parsed.results,
            credits=float(credits) if credits is not None else None,
        )
