import asyncio
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from components.candidate_selector import content_words, detail_overlap
from components.search_runner import parse_published
from components.text_extractor import (
    ExtractedText,
    ExtractionError,
    content_hash,
    detect_language,
    extract_page,
    split_sentences,
)
from config.settings import (
    MAX_DOCUMENTS_PER_CLAIM,
    MAX_DOCUMENTS_PER_DOMAIN,
    MAX_EXTRACTED_CHARS,
    MAX_FETCH_ATTEMPTS_PER_CLAIM,
)
from schemas.claim import AtomicClaim
from schemas.document import Document, Sentence
from schemas.search import Candidate
from services.limits import Limits
from services.url_safety import FetchError, SafeFetcher, UnsafeURL, canonicalize, domain_of

MIN_WORDS = 100
LOGIN_WALL = re.compile(
    r"\b(sign in to continue|log in to continue|subscribe to continue|enable javascript|"
    r"access denied|page not found|404 not found)\b",
    re.IGNORECASE,
)


@dataclass
class FetchLog:
    url: str
    decision: str
    reason: str


@dataclass
class FetchBatch:
    documents: list[tuple[Document, Candidate]] = field(default_factory=list)
    log: list[FetchLog] = field(default_factory=list)
    attempts: int = 0


def clearly_unrelated(text: str, claim: AtomicClaim) -> bool:
    """True when neither the main subject nor any key detail appears."""
    details = [d.text for d in claim.key_details]
    if detail_overlap(text, details) > 0:
        return False
    lowered = text.lower()
    words = content_words(claim.text)
    return sum(w in lowered for w in words) < max(2, len(words) // 3)


class DocumentStore:
    """Holds an audit's documents, so claims can share a fetched URL."""

    def __init__(self, audit_id: str):
        self.audit_id = audit_id
        self.by_url: dict[str, Document] = {}
        self.by_id: dict[str, Document] = {}
        self.count = 0
        self.blocked_domains: set[str] = set()

    def next_ref(self) -> str:
        self.count += 1
        return f"doc_{self.count:02d}"

    def add(self, document: Document) -> None:
        self.by_url[document.canonical_url] = document
        self.by_id[document.id] = document


class DocumentFetcher:
    def __init__(self, fetcher: SafeFetcher, limits: Limits):
        self.fetcher = fetcher
        self.limits = limits

    async def build_document(
        self, candidate: Candidate, store: DocumentStore
    ) -> Document | str:
        """Return a document, or a rejection reason."""
        hit = candidate.hit
        final_url = hit.canonical_url
        content_type = "html"
        retrieved_via = "direct"
        failure = None
        if hit.domain in store.blocked_domains:
            failure = "domain_blocked"
        else:
            try:
                async with self.limits.web_fetch:
                    page = await self.fetcher.fetch(hit.url)
                extracted = await asyncio.to_thread(extract_page, page)
                final_url = canonicalize(page.url)
                content_type = page.content_type
            except (UnsafeURL, FetchError, ExtractionError) as exc:
                failure = str(exc) or type(exc).__name__
        if failure == "http_403":
            store.blocked_domains.add(hit.domain)
        if failure:
            if not hit.raw_content:
                return failure
            # Text the search provider already holds, used only when direct
            # access fails, and labelled so reports can say where it came from.
            retrieved_via = "tavily"
            extracted = ExtractedText(
                hit.raw_content[:MAX_EXTRACTED_CHARS], hit.title or None, None,
                len(hit.raw_content) > MAX_EXTRACTED_CHARS,
            )

        text = extracted.text
        words = len(text.split())
        if words < MIN_WORDS and candidate.tier != "primary":
            return "too_short"
        if words < 300 and LOGIN_WALL.search(text):
            return "error_or_login_page"

        ref = store.next_ref()
        sentences = [
            Sentence(id=f"{ref}_s{i}", text=s)
            for i, s in enumerate(split_sentences(text), start=1)
        ]
        published_raw = extracted.published_raw or candidate.hit.published_raw
        return Document(
            id=str(uuid.uuid4()),
            ref=ref,
            audit_id=store.audit_id,
            canonical_url=candidate.hit.canonical_url,
            domain=domain_of(final_url),
            title=extracted.title or candidate.hit.title or None,
            content_type=content_type,
            content_hash=content_hash(text),
            text_snapshot=text,
            sentences=sentences,
            language=detect_language(text),
            published_at=parse_published(published_raw),
            published_raw=published_raw,
            retrieved_at=datetime.now(timezone.utc),
            truncated=extracted.truncated,
            retrieved_via=retrieved_via,
        )

    async def collect(
        self,
        selected: list[Candidate],
        replacements: list[Candidate],
        claim: AtomicClaim,
        store: DocumentStore,
    ) -> FetchBatch:
        batch = FetchBatch()
        queue = [*selected, *replacements]
        domains: dict[str, int] = {}

        def accept(document: Document, candidate: Candidate) -> None:
            batch.documents.append((document, candidate))
            domains[candidate.hit.domain] = domains.get(candidate.hit.domain, 0) + 1

        while (
            queue
            and len(batch.documents) < MAX_DOCUMENTS_PER_CLAIM
            and batch.attempts < MAX_FETCH_ATTEMPTS_PER_CLAIM
        ):
            needed = MAX_DOCUMENTS_PER_CLAIM - len(batch.documents)
            room = MAX_FETCH_ATTEMPTS_PER_CLAIM - batch.attempts
            current: list[Candidate] = []
            pending: dict[str, int] = dict(domains)
            remaining = []
            for candidate in queue:
                domain = candidate.hit.domain
                if domain in store.blocked_domains and not candidate.hit.raw_content:
                    batch.log.append(FetchLog(candidate.hit.url, "rejected", "domain_blocked"))
                    continue
                existing = store.by_url.get(candidate.hit.canonical_url)
                if pending.get(domain, 0) >= MAX_DOCUMENTS_PER_DOMAIN:
                    remaining.append(candidate)
                elif existing is not None and len(current) < min(needed, room):
                    pending[domain] = pending.get(domain, 0) + 1
                    current.append(candidate)
                elif existing is None and len(current) < min(needed, room):
                    pending[domain] = pending.get(domain, 0) + 1
                    current.append(candidate)
                else:
                    remaining.append(candidate)
            if not current:
                break
            queue = remaining

            fresh = [c for c in current if c.hit.canonical_url not in store.by_url]
            for candidate in current:
                existing = store.by_url.get(candidate.hit.canonical_url)
                if existing is not None:
                    accept(existing, candidate)
            batch.attempts += len(fresh)
            built = await asyncio.gather(
                *(self.build_document(c, store) for c in fresh)
            )
            for candidate, result in zip(fresh, built):
                if isinstance(result, str):
                    batch.log.append(FetchLog(candidate.hit.url, "rejected", result))
                    continue
                accept(result, candidate)
        return batch
