from prompts import CLASSIFICATION_PROMPT, CLASSIFICATION_PROMPT_VERSION
from schemas.claim import AtomicClaim, ClaimClassification, ClaimType, Domain
from schemas.llm_outputs import ClassificationItem, ClassificationOutput
from services.llm import LLMError, LLMService

DOMAINS = set(Domain.__args__)
CLAIM_TYPES = set(ClaimType.__args__)
STRICTEST_TYPE = "official_record"  # A failure must never lower the evidence standard


def normalize(claim_id: str, item: ClassificationItem | None) -> ClaimClassification:
    if item is None:
        return ClaimClassification(
            claim_id=claim_id, primary_domain="general_news",
            claim_type=STRICTEST_TYPE, fallback=True,
        )
    primary = item.primary_domain if item.primary_domain in DOMAINS else "general_news"
    secondary = [
        d for d in dict.fromkeys(item.secondary_domains) if d in DOMAINS and d != primary
    ][:2]
    valid_type = item.claim_type in CLAIM_TYPES
    claim_type = item.claim_type if valid_type else STRICTEST_TYPE
    return ClaimClassification(
        claim_id=claim_id,
        primary_domain=primary,
        secondary_domains=secondary,
        claim_type=claim_type,
        fallback=not valid_type,
    )


async def classify_claims(
    claims: list[AtomicClaim], llm: LLMService, audit_id: str
) -> dict[str, ClaimClassification]:
    numbered = "\n".join(f"{i}. {c.text}" for i, c in enumerate(claims))
    prompt = CLASSIFICATION_PROMPT.format(
        domains=", ".join(sorted(DOMAINS)),
        claims=numbered,
    )
    try:
        output = await llm.generate_structured(
            prompt, ClassificationOutput, "claim_classification", audit_id, CLASSIFICATION_PROMPT_VERSION
        )
        by_index = {item.claim_index: item for item in output.items}
    except LLMError:
        by_index = {}
    return {c.id: normalize(c.id, by_index.get(i)) for i, c in enumerate(claims)}
