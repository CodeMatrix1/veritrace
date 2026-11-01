import re

from rank_bm25 import BM25Okapi

from components.candidate_selector import STOPWORDS, detail_present
from components.number_compare import parse_numbers
from config.settings import MAX_PASSAGES_PER_CLAIM, MAX_PASSAGES_PER_DOCUMENT
from schemas.claim import AtomicClaim
from schemas.document import Document, Passage

WINDOW = 4
STEP = 3  # A 4-sentence window with a 1-sentence overlap

# "FY2024-25", "FY 2024-25" and "FY 2024–25" all mean the same period, but tokenize
# differently without this: a dash or space between FY and the year, or between the
# two years, must not stop them from matching.
FY_PATTERN = re.compile(r"\bFY\s*\W?\s*(\d{4})\s*\W\s*(\d{2})\b", re.IGNORECASE)
# "5.5%" and "5.5 per cent" must also match, as official documents use both.
PERCENT_PATTERN = re.compile(r"(\d)\s*(?:per\s*cent|percent)\b", re.IGNORECASE)


def normalize_fy(text: str) -> str:
    return FY_PATTERN.sub(lambda m: f"FY{m.group(1)}{m.group(2)}", text)


def tokenize(text: str) -> list[str]:
    normalized = PERCENT_PATTERN.sub(r"\1%", normalize_fy(text)).lower()
    tokens = (t.rstrip(".") for t in re.findall(r"[\w₹%.]+", normalized))
    return [t for t in tokens if t]


def build_passages(document: Document, origins: frozenset[str]) -> list[Passage]:
    sentences = document.sentences
    passages = []
    last_start = max(len(sentences) - 1, 0)
    for start in range(0, last_start + 1, STEP):
        window = sentences[start:start + WINDOW]
        if not window:
            continue
        passages.append(Passage(
            id=f"{document.ref}_p{len(passages) + 1}",
            document_id=document.id,
            sentence_ids=[s.id for s in window],
            text=" ".join(s.text for s in window),
            bm25_score=0.0,
            origins=origins,
        ))
        if start + WINDOW >= len(sentences):
            break
    return passages


def entity_coverage(passage_text: str, claim: AtomicClaim) -> float:
    """Return the share of the claim's entity, time and place details present.

    Numeric details are excluded, so a passage reporting a different figure for
    the same entity and period still competes to be seen, rather than losing to
    a keyword-dense but less relevant passage elsewhere in a large document.
    """
    details = [d.text for d in claim.key_details if not parse_numbers(d.text)]
    if not details:
        return 0.0
    return sum(detail_present(d, passage_text) for d in details) / len(details)


def rank_passages(
    documents: list[tuple[Document, frozenset[str]]], claim: AtomicClaim
) -> list[Passage]:
    """Rank across all documents, then apply document and origin coverage limits."""
    passages = [p for d, origins in documents for p in build_passages(d, origins)]
    if not passages:
        return []
    # Unique terms only: repeating a name in the text and key details would
    # otherwise let generic passages about that name outrank the actual fact.
    terms = tokenize(" ".join([claim.text, *(d.text for d in claim.key_details)]))
    query = [t for t in dict.fromkeys(terms) if t not in STOPWORDS]
    query_terms = set(query)
    bm25 = BM25Okapi([tokenize(p.text) for p in passages])
    scores = bm25.get_scores(query)
    for passage, score in zip(passages, scores):
        # BM25 scores collapse on tiny corpora, so term overlap breaks those ties.
        overlap = len(query_terms & set(tokenize(passage.text)))
        base = float(score) + overlap * 1e-3
        passage.bm25_score = base * (1 + entity_coverage(passage.text, claim))
    ranked = sorted(passages, key=lambda p: p.bm25_score, reverse=True)

    per_document: dict[str, int] = {}
    selected: list[Passage] = []

    def take(passage: Passage) -> bool:
        if passage in selected or len(selected) >= MAX_PASSAGES_PER_CLAIM:
            return False
        if per_document.get(passage.document_id, 0) >= MAX_PASSAGES_PER_DOCUMENT:
            return False
        selected.append(passage)
        per_document[passage.document_id] = per_document.get(passage.document_id, 0) + 1
        return True

    for origin in ("support", "contradiction", "gap"):
        for passage in ranked:
            if origin in passage.origins and take(passage):
                break
    for passage in ranked:
        take(passage)
    return selected
