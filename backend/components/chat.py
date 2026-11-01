import asyncio
import re
from datetime import datetime, timezone
from typing import Any

from adapters.base import SearchAdapter
from components.report_writer import BRACKET_LABEL, sentence_is_grounded
from components.source_router import OFFICIAL_HOST, is_social
from components.text_extractor import ExtractionError, extract_page
from prompts import (
    CHAT_PROMPT,
    CHAT_PROMPT_VERSION,
    CHAT_REWRITE_PROMPT,
    CHAT_REWRITE_PROMPT_VERSION,
)
from schemas.llm_outputs import ChatOutput, RewriteOutput
from schemas.search import SearchRequest
from services.db import Repository
from services.llm import LLMError, LLMService
from services.url_safety import FetchError, SafeFetcher, UnsafeURL, domain_of

MAX_PASSAGES = 8
MAX_PASSAGES_PER_PAGE = 2
CHUNK_CHARS = 900
CHUNK_OVERLAP = 100
MAX_PAGE_CHARS = 100_000
MIN_PAGE_CHARS = 300
LIVE_RESULTS = 5
NOT_FOUND = "I couldn't find a reliable answer to this."
DOCUMENT_NOT_READ = "I couldn't read that page. Try another official link."
OFFICIAL_SEARCH_DOMAINS = ["gov.in", "nic.in", "rbi.org.in", "who.int"]
EXTRA_OFFICIAL = ("rbi.org.in",)
SECTIONS = (
    "Answer", "Who qualifies", "Benefits", "Documents needed", "How to apply",
    "Key dates", "What changed", "Who is affected", "Limits",
)
# A section only appears if the question asks for it, as the model can misuse them.
SECTION_WORDS = {
    "Who qualifies": r"qualif|eligib|who can|who is",
    "Benefits": r"benefit|cover|get|provide",
    "Documents needed": r"document|paper|proof",
    "How to apply": r"apply|file|how|process|register",
    "Key dates": r"when|date|deadline|effect",
    "What changed": r"chang|new|amend|latest|update",
    "Who is affected": r"affect|impact|mean for",
}
OVERVIEW_WORDS = re.compile(r"\b(explain|detail|about|scheme|what is)\b", re.IGNORECASE)
# Saved pages can be out of date, so these questions go to live sources first.
CURRENT_WORDS = re.compile(r"\b(upcoming|latest|new|current|recent|now|today|next)\b", re.IGNORECASE)


def choose_section(section: str, question: str) -> str:
    if section not in SECTIONS:
        return "Answer"
    words = SECTION_WORDS.get(section)
    if words is None or OVERVIEW_WORDS.search(question) or re.search(words, question, re.IGNORECASE):
        return section
    return "Answer"


def readable(text: str) -> str:
    """Tidy page markup for display, keeping the words of a quote."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return " ".join(re.sub(r"\*\*|\|", " ", text).split())


async def standalone_question(
    question: str, history: list[dict[str, str]], llm: LLMService
) -> str:
    """Turn a follow-up such as "explain in detail?" into a full question."""
    if not history:
        return question
    earlier = "\n".join(f'User: {t["question"]}\nAssistant: {t["answer"]}' for t in history)
    try:
        output = await llm.generate_structured(
            CHAT_REWRITE_PROMPT.format(history=earlier, question=question),
            RewriteOutput, "chat_rewrite", None, CHAT_REWRITE_PROMPT_VERSION,
        )
    except LLMError:
        return question
    return output.question.strip()[:500] or question


def is_official(domain: str) -> bool:
    if is_social(domain):
        return False
    return bool(OFFICIAL_HOST.search(domain)) or domain.endswith(EXTRA_OFFICIAL)


def split_chunks(text: str) -> list[str]:
    """Split page text into overlapping passages, without cutting words."""
    words = " ".join(text[:MAX_PAGE_CHARS].split()).split(" ")
    chunks, current, size = [], [], 0
    for word in words:
        current.append(word)
        size += len(word) + 1
        if size >= CHUNK_CHARS:
            chunks.append(" ".join(current))
            kept, kept_size = [], 0
            for tail in reversed(current):
                if kept_size >= CHUNK_OVERLAP:
                    break
                kept.insert(0, tail)
                kept_size += len(tail) + 1
            current, size = kept, kept_size
    if size > CHUNK_OVERLAP or not chunks:
        chunks.append(" ".join(current))
    return chunks


def select_passages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the best passages, without letting one page fill the answer."""
    per_page: dict[str, int] = {}
    selected = []
    for row in rows:
        if per_page.get(row["url"], 0) < MAX_PASSAGES_PER_PAGE:
            per_page[row["url"]] = per_page.get(row["url"], 0) + 1
            selected.append(row)
        if len(selected) == MAX_PASSAGES:
            break
    return selected


def rank_chunks(question: str, page: dict[str, Any], text: str) -> list[dict[str, Any]]:
    terms = {t for t in re.findall(r"\w+", question.lower()) if len(t) > 3}
    chunks = split_chunks(text)
    scored = sorted(
        chunks, key=lambda c: -sum(term in c.lower() for term in terms)
    )
    return [{**page, "text": chunk} for chunk in scored]


async def search_sources(
    question: str, repo: Repository, search: SearchAdapter, blocked: list[str], official: bool
) -> list[dict[str, Any]]:
    """Search live, and save official pages so the next answer is faster."""
    request = SearchRequest(
        claim_id="", purpose="support", scope="official" if official else "open", query=question,
        include_domains=OFFICIAL_SEARCH_DOMAINS if official else [],
        exclude_domains=[] if official else blocked,
        max_results=LIVE_RESULTS,
    )
    result = await search.search(request)
    await repo.record_usage(
        None, "tavily", "chat_search", result.status,
        credits_used=result.credits if result.credits is not None else 1,
    )
    rows: list[list[dict[str, Any]]] = []
    for item in result.results:
        domain = domain_of(item.url)
        text = item.raw_content or ""
        if len(text) < MIN_PAGE_CHARS or is_social(domain) or (official and not is_official(domain)):
            continue
        title = item.title or domain
        if is_official(domain) and not await repo.knowledge_page_exists(item.url):
            await repo.save_knowledge_page(
                item.url, domain, title, item.published_date, split_chunks(text)
            )
        page = {"url": item.url, "title": title, "domain": domain, "published": item.published_date}
        rows.append(rank_chunks(question, page, text))
    # Take the best passages from every page first, so one page can't crowd out the rest.
    return [row for group in zip(*rows) for row in group] if rows else []


async def read_document(
    question: str, url: str, fetcher: SafeFetcher
) -> list[dict[str, Any]]:
    """Read one page or PDF the user linked to, using the same safe fetcher as audits."""
    try:
        page = await fetcher.fetch(url)
        extracted = await asyncio.to_thread(extract_page, page)
    except (UnsafeURL, FetchError, ExtractionError):
        return []
    domain = domain_of(page.url)
    title = extracted.title or domain
    details = {"url": page.url, "title": title, "domain": domain, "published": None}
    return rank_chunks(question, details, extracted.text)


async def compose(
    question: str, rows: list[dict[str, Any]], llm: LLMService, repo_pages: int, mode: str
) -> dict[str, Any] | None:
    """Answer from passages, keeping only sentences that a cited passage backs up."""
    passages = select_passages(rows)
    if not passages:
        return None
    labels = {f"S{i}": row for i, row in enumerate(passages, start=1)}
    blocks = "\n\n".join(
        f'[{label}] {row["domain"]}, {row["title"]}, published {row["published"] or "date unknown"}:'
        f'\n{row["text"]}'
        for label, row in labels.items()
    )
    output = await llm.generate_structured(
        CHAT_PROMPT.format(
            today=datetime.now(timezone.utc).date().isoformat(), question=question, passages=blocks
        ),
        ChatOutput, "chat", None, CHAT_PROMPT_VERSION,
    )

    if not output.in_scope:
        return None

    sections: dict[str, list[str]] = {}
    cited: dict[str, dict[str, Any]] = {}
    for sentence in output.sentences:
        rows_cited = [labels[label] for label in sentence.labels if label in labels]
        text = BRACKET_LABEL.sub("", sentence.text).strip()
        # A figure must appear in the passages this sentence cites, or in the question.
        if rows_cited and sentence_is_grounded(text, [r["text"] for r in rows_cited], question):
            sections.setdefault(choose_section(sentence.section, question), []).append(text)
            cited.update({r["url"]: r for r in rows_cited})
    if not sections:
        return None
    ordered = [
        {"title": title, "sentences": sections[title]} for title in SECTIONS if title in sections
    ]
    return {
        "found": True,
        "mode": mode,
        "answer": " ".join(s for section in ordered for s in section["sentences"]),
        "sections": ordered,
        "sources": [
            {
                "title": row["title"],
                "url": row["url"],
                "publisher": row["domain"],
                "published": row["published"],
                "official": is_official(row["domain"]),
                "quote": readable(row["text"]),
            }
            for row in cited.values()
        ],
        "pages": repo_pages,
        "understood_as": question,
    }


def nothing(message: str, pages: int) -> dict[str, Any]:
    return {
        "found": False, "mode": "none", "answer": message, "sections": [], "sources": [],
        "pages": pages,
    }


async def answer_question(
    question: str,
    repo: Repository,
    llm: LLMService,
    search: SearchAdapter | None = None,
    fetcher: SafeFetcher | None = None,
    document_url: str | None = None,
    blocked: list[str] | None = None,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    pages = await repo.count_knowledge_pages()
    question = await standalone_question(question, history or [], llm)
    if document_url:
        rows = await read_document(question, document_url, fetcher) if fetcher else []
        if not rows:
            return nothing(DOCUMENT_NOT_READ, pages)
        reply = await compose(question, rows, llm, pages, "document")
        return reply or nothing("That page doesn't answer this question.", pages)

    async def saved() -> dict[str, Any] | None:
        return await compose(question, await repo.search_knowledge(question), llm, pages, "saved")

    async def live() -> dict[str, Any] | None:
        if search is None:
            return None
        rows = await search_sources(question, repo, search, blocked or [], official=True)
        return await compose(question, rows, llm, pages, "live")

    async def web() -> dict[str, Any] | None:
        if search is None:
            return None
        rows = await search_sources(question, repo, search, blocked or [], official=False)
        return await compose(question, rows, llm, pages, "web")

    async def earlier_sources() -> dict[str, Any] | None:
        urls = [url for turn in history or [] for url in turn.get("source_urls", [])]
        if not urls:
            return None
        rows = await repo.knowledge_passages(question, urls)
        return await compose(question, rows, llm, pages, "saved")

    steps = (live, saved, web) if CURRENT_WORDS.search(question) else (saved, live, web)
    steps = (earlier_sources, *steps)
    for step in steps:
        reply = await step()
        if reply:
            return reply
    return nothing(NOT_FOUND, pages)
