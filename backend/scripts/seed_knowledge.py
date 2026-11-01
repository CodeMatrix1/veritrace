"""Fill the chatbot's saved pages from official sites.

Run from backend/ with: python -m scripts.seed_knowledge ../.env
It first reuses official pages already in the search cache, which costs nothing, then runs
the searches below at about 1 Tavily credit each. Text comes from Tavily, as many of these
sites block direct fetching. Pages already saved are skipped.
"""
import asyncio
import sys
from pathlib import Path

from adapters.tavily import TavilyAdapter
from components.chat import is_official, split_chunks
from schemas.search import RawResult, SearchRequest
from services.db import Repository, apply_migrations
from services.url_safety import domain_of

MIN_PAGE_CHARS = 300
SEARCHES = {
    "rbi.org.in": [
        "monetary policy statement repo rate", "inflation target framework",
        "foreign exchange reserves", "digital rupee pilot", "UPI payment systems report",
    ],
    "mospi.gov.in": [
        "provisional estimates of GDP press note", "consumer price index press release",
        "Periodic Labour Force Survey unemployment rate", "index of industrial production",
        "Annual Survey of Industries results",
    ],
    "pib.gov.in": [
        "Ayushman Bharat beneficiaries", "PM-KISAN instalment farmers", "Jan Dhan accounts",
        "Swachh Bharat Mission achievements", "PRAGATI meeting projects reviewed",
        "Union Budget highlights", "pension scheme government",
    ],
    "indiabudget.gov.in": ["Economic Survey fiscal deficit", "Budget at a glance"],
    "isro.gov.in": [
        "Chandrayaan-3 mission", "Aditya-L1 mission", "Gaganyaan programme",
        "Mars Orbiter Mission", "PSLV launch",
    ],
    "who.int": [
        "mpox public health emergency", "tuberculosis India", "diabetes fact sheet",
        "vaccines and autism", "traditional medicine strategy",
    ],
    "indiacode.nic.in": ["Digital Personal Data Protection Act", "Consumer Protection Act"],
    "eci.gov.in": ["Lok Sabha general election voter turnout"],
    "myscheme.gov.in": ["PM-KISAN eligibility benefits documents", "scholarship for undergraduate students"],
    "scholarships.gov.in": ["national scholarship portal eligibility"],
    "startupindia.gov.in": ["startup recognition eligibility benefits", "registration and approvals for startups"],
    "msme.gov.in": ["MSME schemes credit guarantee", "Udyam registration"],
    "rti.gov.in": ["how to file an RTI application fees first appeal"],
    "consumeraffairs.nic.in": ["consumer complaint how to file"],
    "passportindia.gov.in": ["passport application documents required"],
    "ugc.gov.in": ["UGC regulations notification students"],
}


async def save(repo: Repository, result: RawResult, text: str | None) -> bool:
    domain = domain_of(result.url)
    if not text or len(text) < MIN_PAGE_CHARS or not is_official(domain):
        return False
    if await repo.knowledge_page_exists(result.url):
        return False
    await repo.save_knowledge_page(
        result.url, domain, result.title or domain, result.published_date, split_chunks(text)
    )
    return True


async def from_cache(repo: Repository) -> int:
    rows = await repo.pool.fetch("SELECT response FROM tool_cache WHERE status = 'ok'")
    saved = 0
    for row in rows:
        results = [RawResult(**r) for r in row["response"]["results"]]
        texts = await repo.page_texts([r.url for r in results])
        for result in results:
            saved += await save(repo, result, texts.get(result.url))
    return saved


async def from_searches(repo: Repository, api_key: str) -> tuple[int, int]:
    adapter = TavilyAdapter(api_key)
    saved = searches = 0
    for domain, queries in SEARCHES.items():
        for query in queries:
            request = SearchRequest(
                claim_id="", purpose="support", scope="official", query=query,
                include_domains=[domain],
            )
            result = await adapter.search(request)
            searches += 1
            await repo.record_usage(
                None, "tavily", "seed_knowledge", result.status,
                credits_used=result.credits if result.credits is not None else 1,
            )
            found = [await save(repo, r, r.raw_content) for r in result.results]
            print(f"{domain:22}{query:46}{result.status:8}{sum(found)} new", flush=True)
            saved += sum(found)
    await adapter.close()
    return saved, searches


async def main() -> None:
    env = dict(
        line.split("=", 1)
        for line in Path(sys.argv[1]).read_text("utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    )
    url = env["DATABASE_URL"].strip().strip('"')
    await apply_migrations(url)
    repo = await Repository.connect(url)
    print("from cache:", await from_cache(repo), "new pages", flush=True)
    saved, searches = await from_searches(repo, env["TAVILY_API_KEY"].strip().strip('"'))
    print("from", searches, "searches:", saved, "new pages")
    print("total pages:", await repo.count_knowledge_pages())
    await repo.close()


if __name__ == "__main__":
    asyncio.run(main())
