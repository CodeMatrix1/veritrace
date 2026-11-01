import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent))

from components.document_fetcher import DocumentFetcher  # noqa: E402
from components.search_runner import SearchRunner  # noqa: E402
from config.loader import load_config  # noqa: E402
from config.settings import Settings  # noqa: E402
from pipeline.orchestrator import Deps  # noqa: E402
from schemas.search import AdapterResult, RawResult  # noqa: E402
from services.cache import SearchCache  # noqa: E402
from services.db import Repository, apply_migrations  # noqa: E402
from services.limits import Limits  # noqa: E402
from services.llm import LLMError, LLMResponse, LLMService, TransientLLMError  # noqa: E402
from services.trace import Tracer  # noqa: E402
from services.url_safety import SafeFetcher  # noqa: E402

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


class ScriptedClient:
    """Answers each LLM purpose from a script. A value may be a dict, str or Exception."""

    provider = "scripted"
    model = "scripted"

    def __init__(self, script: dict):
        self.script = script
        self.calls: list[str] = []
        self.prompts: dict[str, list[str]] = {}

    async def generate(self, system, prompt, schema, purpose) -> LLMResponse:
        self.calls.append(purpose)
        self.prompts.setdefault(purpose, []).append(prompt)
        value = self.script.get(purpose)
        if isinstance(value, list):
            value = value.pop(0) if value else None
        if callable(value):
            value = value(prompt)
        if isinstance(value, (LLMError, TransientLLMError)):
            raise value
        if value is None:
            value = DEFAULTS[purpose]
        text = value if isinstance(value, str) else json.dumps(value)
        return LLMResponse(text=text, input_tokens=10, output_tokens=5)


DEFAULTS = {
    "claim_classification": {"items": []},
    "query_planning": {"support_query": "support q", "contradiction_query": "contra q"},
    "gap_queries": {"queries": []},
    "skeptic_review": {"issues": []},
    "summary": {"sentences": []},
    "evidence_analysis": {"items": []},
}


@dataclass
class FakeSearch:
    name: str = "tavily"
    responses: dict = field(default_factory=dict)
    default: AdapterResult | None = None
    requests: list = field(default_factory=list)

    async def search(self, req) -> AdapterResult:
        self.requests.append(req)
        value = self.responses.get(req.query, self.default)
        if isinstance(value, list):
            value = value.pop(0)
        if value is None:
            return AdapterResult(status="empty")
        return value

    async def close(self) -> None:
        pass


def result(url: str, title: str = "Title", content: str = "") -> RawResult:
    return RawResult(url=url, title=title, content=content)


def ok(*urls: str) -> AdapterResult:
    return AdapterResult(status="ok", results=[result(u) for u in urls], credits=1.0)


def page_transport(pages: dict[str, str | bytes | int]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        value = pages.get(str(request.url))
        if value is None:
            return httpx.Response(404)
        if isinstance(value, int):
            return httpx.Response(value)
        return httpx.Response(200, text=value, headers={"content-type": "text/html"})

    return httpx.MockTransport(handler)


async def public_resolver(host: str) -> list[str]:
    return ["93.184.216.34"]


def article(*sentences: str, title: str = "Report") -> str:
    filler = " ".join(
        f"This background sentence number {i} describes unrelated administrative matters."
        for i in range(40)
    )
    body = "".join(f"<p>{s}</p>" for s in sentences)
    return (
        f"<html><head><title>{title}</title></head>"
        f"<body><article><h1>{title}</h1>{body}<p>{filler}</p></article></body></html>"
    )


@pytest_asyncio.fixture
async def repo():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    await apply_migrations(TEST_DATABASE_URL)
    repository = await Repository.connect(TEST_DATABASE_URL)
    await repository.pool.execute("TRUNCATE audits, tool_cache, page_texts, knowledge_pages, limit_resets, usage CASCADE")
    yield repository
    await repository.close()


@pytest.fixture
def make_deps(repo):
    def build(script: dict, search: FakeSearch, pages: dict, **settings) -> Deps:
        limits = Limits()
        config = load_config()
        settings.setdefault("report_reuse_days", 0)
        app_settings = Settings(**settings)
        fetcher = SafeFetcher(transport=page_transport(pages), resolver=public_resolver)
        client = ScriptedClient(script)
        deps = Deps(
            repo=repo,
            llm=LLMService(client, repo, limits, backoff_seconds=0),
            runner=SearchRunner(
                {"tavily": search}, SearchCache(repo), repo, limits, app_settings,
                retry_delay=0,
            ),
            documents=DocumentFetcher(fetcher, limits),
            fetcher=fetcher,
            tracer=Tracer(repo),
            config=config,
            settings=app_settings,
        )
        deps.client = client
        return deps

    return build
