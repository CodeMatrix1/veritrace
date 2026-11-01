import re
from datetime import datetime

from components.source_router import official_domains
from config.loader import AppConfig
from config.settings import MAX_GAP_QUERIES, MAX_QUERY_WORDS, RESULTS_PER_SEARCH
from prompts import (
    CONTRADICTION_FOCUS,
    GAP_DESCRIPTIONS,
    GAP_QUERY_PROMPT,
    INITIAL_QUERY_PROMPT,
    QUERY_PROMPT_VERSION,
)
from schemas.claim import AtomicClaim, ClaimClassification
from schemas.llm_outputs import InitialQueryOutput, QueryOutput
from schemas.search import SearchRequest
from services.cache import is_recent
from services.llm import LLMError, LLMService

BREAKING_DAYS = 3
EXCEPTION_WORDS = re.compile(r"\b(all|never|only|first|every|always)\b", re.IGNORECASE)


def describe_gap(gap: str, claim: AtomicClaim) -> str:
    details = {d.id: d.text for d in claim.key_details}
    if gap in details:
        return f"a source that directly states: {details[gap]}"
    return GAP_DESCRIPTIONS.get(gap, "more direct evidence")


def _clean(query: str) -> str:
    return " ".join(query.split()[:MAX_QUERY_WORDS])


def template_queries(claim: AtomicClaim) -> tuple[str, str, str, str]:
    """Return official support, open support, open and official contradiction."""
    # The claim text keeps the topic, which key details alone can lose.
    return (
        _clean(claim.text),
        _clean(claim.text),
        _clean(f"{claim.text} exception limit denied"),
        _clean(f"{claim.text} clarification correction"),
    )


async def generate_initial_queries(
    claim: AtomicClaim,
    classification: ClaimClassification,
    llm: LLMService,
    audit_id: str,
) -> InitialQueryOutput | None:
    exceptions = ""
    if EXCEPTION_WORDS.search(claim.text):
        exceptions = " The claim uses an absolute word, so look for exceptions."
    prompt = INITIAL_QUERY_PROMPT.format(
        focus=CONTRADICTION_FOCUS.get(classification.claim_type, "corrections and denials"),
        exceptions=exceptions,
        max_words=MAX_QUERY_WORDS,
        claim=claim.text,
    )
    try:
        return await llm.generate_structured(
            prompt, InitialQueryOutput, "query_planning", audit_id, QUERY_PROMPT_VERSION
        )
    except LLMError:
        return None


async def plan_initial_searches(
    claim: AtomicClaim,
    classification: ClaimClassification,
    config: AppConfig,
    llm: LLMService,
    audit_id: str,
    now: datetime,
) -> list[SearchRequest]:
    official_support, open_support, open_contra, official_contra = template_queries(claim)
    generated = await generate_initial_queries(claim, classification, llm, audit_id)
    if generated is not None:
        open_support = _clean(generated.support_query) or open_support
        open_contra = _clean(generated.contradiction_query) or open_contra

    domains = official_domains(classification, config)
    blocked = list(config.sources.blocked)

    def request(purpose: str, query: str, official: bool) -> SearchRequest:
        return SearchRequest(
            claim_id=claim.id,
            purpose=purpose,
            scope="official" if official and domains else "open",
            query=query,
            include_domains=domains if official else [],
            exclude_domains=[] if official and domains else blocked,
            max_results=RESULTS_PER_SEARCH,
            mandatory=True,
        )

    official_first = request("support", official_support, True)
    open_second = request("support", open_support, False)
    support = [official_first, open_second]
    if is_recent(claim, now, BREAKING_DAYS):
        support.reverse()
    return [
        *support,
        request("contradiction", open_contra, False),
        request("contradiction", official_contra, True),
    ]


async def plan_gap_searches(
    claim: AtomicClaim,
    gaps: list[str],
    classification: ClaimClassification,
    config: AppConfig,
    llm: LLMService,
    audit_id: str,
    tried: list[str],
) -> list[SearchRequest]:
    described = "\n".join(f"- {describe_gap(g, claim)}" for g in gaps)
    prompt = GAP_QUERY_PROMPT.format(
        count=MAX_GAP_QUERIES,
        max_words=MAX_QUERY_WORDS,
        gaps=described,
        tried="\n".join(f"- {query}" for query in tried),
        claim=claim.text,
    )
    try:
        output = await llm.generate_structured(
            prompt, QueryOutput, "gap_queries", audit_id, QUERY_PROMPT_VERSION
        )
    except LLMError:
        return []

    domains = official_domains(classification, config)
    official = "primary_source" in gaps and bool(domains)
    return [
        SearchRequest(
            claim_id=claim.id,
            purpose="gap",
            scope="official" if official else "open",
            query=_clean(query),
            include_domains=domains if official else [],
            exclude_domains=[] if official else list(config.sources.blocked),
            max_results=RESULTS_PER_SEARCH,
        )
        for query in output.queries[:MAX_GAP_QUERIES]
        if query.strip() and _clean(query) not in tried
    ]
