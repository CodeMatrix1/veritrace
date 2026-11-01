import hashlib
import re
from dataclasses import dataclass

import pymupdf
import trafilatura

from config.settings import MAX_EXTRACTED_CHARS, MAX_PDF_PAGES
from services.url_safety import FetchedPage

ABBREVIATIONS = re.compile(
    r"(?:\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|No|Rs|vs|etc|e\.g|i\.e|Govt|Dept|Inc|Ltd|Co)\.)$"
)
SENTENCE_BREAK = re.compile(r"(?<=[.!?।])[\"')\]]*\s+|\n+")
BLANK_LINE = re.compile(r"\n[ \t]*\n")


class ExtractionError(Exception):
    pass


@dataclass
class ExtractedText:
    text: str
    title: str | None
    published_raw: str | None
    truncated: bool


def _limit(text: str) -> tuple[str, bool]:
    text = text.replace("\x00", "")
    if len(text) > MAX_EXTRACTED_CHARS:
        return text[:MAX_EXTRACTED_CHARS], True
    return text, False


def extract_html(body: bytes, encoding: str | None, url: str) -> ExtractedText:
    html = body.decode(encoding or "utf-8", errors="replace")
    options = {"url": url, "include_comments": False, "include_tables": True}
    text = trafilatura.extract(html, **options)
    if not text or not text.strip():
        text = trafilatura.extract(html, favor_recall=True, **options)
    if not text or not text.strip():
        raise ExtractionError("empty_extraction")
    metadata = trafilatura.extract_metadata(html, default_url=url)
    limited, truncated = _limit(text.strip())
    return ExtractedText(
        text=limited,
        title=metadata.title if metadata else None,
        published_raw=metadata.date if metadata else None,
        truncated=truncated,
    )


def merge_pdf_line_wraps(text: str) -> str:
    """PyMuPDF puts a newline after every visually wrapped line, not only at the
    end of a paragraph, which cuts real sentences in half. Collapse those line
    wraps to spaces, but keep blank lines as the paragraph breaks they are, so
    split_sentences() still treats real section boundaries as hard breaks.

    Unlike PDF text, HTML extracted by trafilatura already uses a single
    newline to mark each block (heading, each paragraph) with no blank-line
    distinction, so this must not be applied there.
    """
    marked = BLANK_LINE.sub("\x00", text)
    return marked.replace("\n", " ").replace("\x00", "\n\n")


def extract_pdf(body: bytes) -> ExtractedText:
    try:
        with pymupdf.open(stream=body, filetype="pdf") as doc:
            pages = [page.get_text() for page in doc.pages(0, min(doc.page_count, MAX_PDF_PAGES))]
            page_count = doc.page_count
            title = (doc.metadata or {}).get("title") or None
            created = (doc.metadata or {}).get("creationDate") or None
    except (RuntimeError, ValueError) as exc:
        raise ExtractionError("unreadable_pdf") from exc
    text = merge_pdf_line_wraps("\n".join(pages)).strip()
    if len(text) < 20:
        raise ExtractionError("unreadable_pdf")
    published = None
    if created and re.match(r"D:\d{8}", created):
        published = f"{created[2:6]}-{created[6:8]}-{created[8:10]}"
    limited, truncated = _limit(text)
    return ExtractedText(limited, title, published, truncated or page_count > MAX_PDF_PAGES)


def extract_page(page: FetchedPage) -> ExtractedText:
    if page.content_type == "pdf":
        return extract_pdf(page.body)
    return extract_html(page.body, page.encoding, page.url)


def split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    pending = ""
    position = 0
    for match in SENTENCE_BREAK.finditer(text):
        chunk = text[position:match.end()]
        position = match.end()
        pending += chunk
        if "\n" not in match.group(0) and ABBREVIATIONS.search(pending.rstrip()):
            continue
        cleaned = " ".join(pending.split())
        if cleaned:
            sentences.append(cleaned)
        pending = ""
    pending += text[position:]
    cleaned = " ".join(pending.split())
    if cleaned:
        sentences.append(cleaned)
    return sentences


def content_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def detect_language(text: str) -> str:
    """Only English is supported in V1, so this just separates English-like text."""
    letters = [c for c in text[:5000] if c.isalpha()]
    if not letters:
        return "unknown"
    latin = sum(c.isascii() for c in letters)
    return "en" if latin / len(letters) >= 0.7 else "other"
