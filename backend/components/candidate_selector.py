import re
from dataclasses import dataclass

from components.search_runner import SearchOutcome
from components.source_router import is_blocked, source_tier
from config.loader import AppConfig
from config.settings import MAX_DOCUMENTS_PER_CLAIM, MAX_DOCUMENTS_PER_DOMAIN
from schemas.claim import AtomicClaim
from schemas.search import Candidate, SearchHit
from services.url_safety import precheck_url

TIER_BONUS = {"primary": 1.0, "secondary": 0.5, "unknown": 0.0}
STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "is", "was", "were",
    "are", "by", "with", "that", "this", "it", "at", "from", "as", "has", "have", "had",
}


@dataclass
class FilterRecord:
    url: str
    decision: str  # kept, rejected or merged
    reason: str


def content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9₹%.,]+", text.lower())
    return {w.strip(".,") for w in words if w.strip(".,") not in STOPWORDS and len(w) > 1}


def detail_present(detail: str, text: str) -> bool:
    lowered = text.lower()
    if detail.lower() in lowered:
        return True
    words = content_words(detail)
    return bool(words) and all(w in lowered for w in words)


def detail_overlap(text: str, details: list[str]) -> float:
    if not details:
        return 0.0
    return sum(detail_present(d, text) for d in details) / len(details)


def candidate_score(
    hit: SearchHit, claim: AtomicClaim, config: AppConfig
) -> tuple[float, str]:
    text = f"{hit.title} {hit.snippet}"
    score = detail_overlap(text, [d.text for d in claim.key_details]) * 3
    tier = source_tier(hit.domain, config)
    score += TIER_BONUS[tier]
    score += 1 / max(hit.rank, 1)
    return score, tier


def build_candidates(
    outcomes: list[SearchOutcome],
    claim: AtomicClaim,
    config: AppConfig,
) -> tuple[list[Candidate], list[FilterRecord]]:
    by_url: dict[str, Candidate] = {}
    records: list[FilterRecord] = []
    for outcome in outcomes:
        for hit in outcome.hits:
            reason = precheck_url(hit.url)
            if reason is None and is_blocked(hit.domain, config):
                reason = "blocked_domain"
            if reason:
                records.append(FilterRecord(hit.url, "rejected", reason))
                continue
            score, tier = candidate_score(hit, claim, config)
            existing = by_url.get(hit.canonical_url)
            if existing is not None:
                existing.origins.add(outcome.request.purpose)
                records.append(FilterRecord(hit.url, "merged", "duplicate_canonical_url"))
                if score > existing.score:
                    existing.hit, existing.score = hit, score
                continue
            by_url[hit.canonical_url] = Candidate(
                hit=hit, tier=tier, score=score, origins={outcome.request.purpose}
            )
    return list(by_url.values()), records


def select_candidates(
    candidates: list[Candidate],
) -> tuple[list[Candidate], list[Candidate]]:
    """Return up to 5 selected candidates, plus the rest as replacements."""
    ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
    selected: list[Candidate] = []
    domains: dict[str, int] = {}

    def fits(c: Candidate) -> bool:
        return (
            c not in selected
            and domains.get(c.hit.domain, 0) < MAX_DOCUMENTS_PER_DOMAIN
            and len(selected) < MAX_DOCUMENTS_PER_CLAIM
        )

    def take(c: Candidate) -> None:
        selected.append(c)
        domains[c.hit.domain] = domains.get(c.hit.domain, 0) + 1

    slots = (
        lambda c: c.tier == "primary",
        lambda c: "contradiction" in c.origins,
        lambda c: "support" in c.origins,
    )
    for matches in slots:
        match = next((c for c in ranked if matches(c) and fits(c)), None)
        if match is not None:
            take(match)
    for c in ranked:
        if fits(c):
            take(c)
    replacements = [c for c in ranked if c not in selected]
    return selected, replacements
