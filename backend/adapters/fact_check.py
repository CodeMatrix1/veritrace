import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from schemas.search import AdapterResult, RawResult, SearchRequest

API_URL = "https://factchecktools.googleapis.com/v1alpha1/claims:search"


class ClaimReview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str | None = None
    title: str | None = None
    textualRating: str | None = None
    reviewDate: str | None = None


class FactCheckClaim(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str = ""
    claimReview: list[ClaimReview] = Field(default_factory=list)


class FactCheckResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    claims: list[FactCheckClaim] = Field(default_factory=list)


class FactCheckAdapter:
    name = "fact_check"

    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = api_key
        self.client = httpx.AsyncClient(transport=transport, timeout=10.0)

    async def close(self) -> None:
        await self.client.aclose()

    async def search(self, req: SearchRequest) -> AdapterResult:
        params = {
            "query": req.query,
            "key": self.api_key,
            "pageSize": req.max_results,
        }
        try:
            response = await self.client.get(API_URL, params=params)
        except httpx.HTTPError:
            return AdapterResult(status="error")
        if response.status_code != 200:
            return AdapterResult(status="error")
        try:
            parsed = FactCheckResponse.model_validate_json(response.content)
        except ValidationError:
            return AdapterResult(status="error")

        results = [
            RawResult(
                url=review.url,
                title=review.title or claim.text,
                content=f"{claim.text} Rating: {review.textualRating or 'unknown'}",
                published_date=review.reviewDate,
            )
            for claim in parsed.claims
            for review in claim.claimReview
            if review.url
        ]
        return AdapterResult(status="ok" if results else "empty", results=results)
