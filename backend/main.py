import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from adapters.fact_check import FactCheckAdapter
from adapters.tavily import TavilyAdapter
from api.routes import router
from components.document_fetcher import DocumentFetcher
from components.search_runner import SearchRunner
from config.loader import load_config
from config.settings import MAX_CONCURRENT_AUDITS, load_settings
from pipeline.orchestrator import Deps, run_audit
from services.cache import SearchCache
from services.db import Repository, apply_migrations
from services.limits import Limits
from services.llm import GroqClient, LLMService
from services.trace import Tracer
from services.url_safety import SafeFetcher

logger = logging.getLogger(__name__)
INTERRUPTED_MESSAGE = "This audit was interrupted. Please submit it again."
DAY_SECONDS = 24 * 60 * 60


async def audit_worker(queue: asyncio.Queue, deps: Deps) -> None:
    while True:
        audit_id = await queue.get()
        try:
            await run_audit(audit_id, deps)
        except Exception:
            logger.exception("Worker failed on audit %s", audit_id)
        finally:
            queue.task_done()


async def retention_loop(repo: Repository) -> None:
    while True:
        try:
            audits, cache = await repo.delete_expired()
            logger.info("Retention removed %s audits and %s cache rows", audits, cache)
        except Exception:
            logger.exception("Retention job failed")
        await asyncio.sleep(DAY_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    config = load_config()
    missing = [
        name
        for name, value in (
            ("DATABASE_URL", settings.database_url),
            ("GROQ_API_KEY", settings.groq_api_key),
            ("TAVILY_API_KEY", settings.tavily_api_key),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")
    await apply_migrations(settings.database_url)
    repo = await Repository.connect(settings.database_url)
    await repo.compress_page_texts()
    limits = Limits()
    fetcher = SafeFetcher()
    adapters = {"tavily": TavilyAdapter(settings.tavily_api_key)}
    fact_check = None
    if settings.fact_check_api_key:
        fact_check = FactCheckAdapter(settings.fact_check_api_key)
        adapters["fact_check"] = fact_check
    llm_client = GroqClient(
        settings.groq_api_key, settings.groq_model,
        fallback_model=settings.groq_fallback_model or None,
    )
    deps = Deps(
        repo=repo,
        llm=LLMService(llm_client, repo, limits),
        runner=SearchRunner(adapters, SearchCache(repo), repo, limits, settings),
        documents=DocumentFetcher(fetcher, limits),
        fetcher=fetcher,
        tracer=Tracer(repo),
        config=config,
        settings=settings,
        fact_check=fact_check,
    )
    queue: asyncio.Queue[str] = asyncio.Queue()
    app.state.deps = deps
    app.state.queue = queue

    await repo.fail_interrupted_audits(INTERRUPTED_MESSAGE)
    for audit_id in await repo.queued_audit_ids():
        queue.put_nowait(audit_id)
    tasks = [
        asyncio.create_task(audit_worker(queue, deps))
        for _ in range(MAX_CONCURRENT_AUDITS)
    ]
    tasks.append(asyncio.create_task(retention_loop(repo)))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for adapter in adapters.values():
            await adapter.close()
        await llm_client.close()
        await fetcher.close()
        await repo.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Veritrace", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[load_settings().frontend_origin],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.include_router(router)
    return app


app = create_app()
