import logging
import re
import uuid
from datetime import date

from components.number_compare import parse_numbers
from config.settings import MAX_CLAIMS, MAX_INPUT_CHARS
from prompts import EXTRACTION_PROMPT, EXTRACTION_PROMPT_VERSION
from schemas.claim import AtomicClaim, ExtractionResult, KeyDetail
from schemas.llm_outputs import ExtractedClaim, ExtractionOutput
from services.llm import LLMError, LLMService

logger = logging.getLogger(__name__)

# A claim such as "The mission was launched by NASA" can match the wrong subject
# entirely, so it must name what it is about before it can be searched.
# "The government" and similar are clear in a public claim, unlike "the mission".
VAGUE_SUBJECT = re.compile(
    r"^(?:(?:it|they|he|she|this|that|these|those)\b"
    r"|(?:the|this|that|these|those)\s+(?!government\b|centre\b|parliament\b)[a-z]+\s+"
    r"(?:was|were|is|are|has|have|had|did|will|would|can|could|claimed|said|showed|shows)\b)"
)


# Rewording can hide a vague subject, as in "NASA launched the mission".
VAGUE_THING = (
    r"(?:mission|project|scheme|photo|video|image|post|message|report|plan|policy|study|"
    r"programme|program|survey|incident|event|decision)"
)
VAGUE_ENDING = re.compile(rf"\b(?:the|this|that)\s+{VAGUE_THING}\s*[.?!]?$", re.IGNORECASE)
UNNAMED_ITEM = re.compile(rf"^(?:a|an)\s+(?:[a-z]+\s+)?{VAGUE_THING}\b", re.IGNORECASE)


def _normal(text: str) -> str:
    return " ".join(text.split()).lower()


def validation_error(item: ExtractedClaim, input_text: str) -> str | None:
    if _normal(item.source_text) not in _normal(input_text):
        return "source_text is not in the input"
    if item.kind != "factual":
        return None
    if not item.text.strip():
        return "factual claim has empty text"
    text = item.text.strip()
    if (
        VAGUE_SUBJECT.match(text[0].lower() + text[1:])
        or VAGUE_ENDING.search(text)
        or UNNAMED_ITEM.match(text)
    ):
        return f'"{item.text}" does not name its subject. Use the name from the input.'
    return None


def complete_key_details(item: ExtractedClaim) -> list[str]:
    """Add any figure stated in the claim that the model left out of key_details."""
    present = {n.value for d in item.key_details for n in parse_numbers(d)}
    missing = [n.original_text for n in parse_numbers(item.text) if n.value not in present]
    return [*item.key_details, *missing]


def checkability(item: ExtractedClaim) -> int:
    score = 0
    if re.search(r"\d", item.text):
        score += 2
    if item.time:
        score += 2
    score += len(re.findall(r"\b[A-Z][a-z]+", item.text[1:]))
    return score


def _build_claim(position: int, item: ExtractedClaim) -> AtomicClaim:
    details = [
        KeyDetail(id=f"c{position}_d{i}", text=text.strip())
        for i, text in enumerate(complete_key_details(item), start=1)
        if text.strip()
    ]
    numbers = [n for d in details for n in parse_numbers(d.text)]
    return AtomicClaim(
        id=str(uuid.uuid4()),
        position=position,
        text=item.text.strip(),
        source_text=item.source_text,
        kind=item.kind,
        key_details=details,
        numbers=numbers,
        time=item.time,
        location=item.location,
    )


async def extract_claims(
    text: str, reference_date: date, llm: LLMService, audit_id: str
) -> ExtractionResult:
    # Long pages exceed the model's per-minute limit, and the opening usually holds the claims.
    text = text[:MAX_INPUT_CHARS]
    prompt = EXTRACTION_PROMPT.format(reference_date=reference_date.isoformat(), feedback="", text=text)
    output = await llm.generate_structured(
        prompt, ExtractionOutput, "claim_extraction", audit_id, EXTRACTION_PROMPT_VERSION
    )
    errors = [validation_error(c, text) for c in output.claims]
    if any(errors):
        feedback = "Your last attempt had these problems: " + "; ".join(
            sorted({e for e in errors if e})
        )
        prompt = EXTRACTION_PROMPT.format(
            reference_date=reference_date.isoformat(), feedback=feedback, text=text
        )
        try:
            output = await llm.generate_structured(
                prompt, ExtractionOutput, "claim_extraction", audit_id, EXTRACTION_PROMPT_VERSION
            )
        except LLMError:
            # The first attempt is still usable. Its invalid items are handled below.
            logger.warning("Extraction retry failed, keeping the first attempt")

    items = []
    for item in output.claims:
        if validation_error(item, text):
            item = item.model_copy(update={"kind": "not_checkable"})
        items.append(item)

    factual = [c for c in items if c.kind == "factual"]
    chosen = sorted(factual, key=checkability, reverse=True)[:MAX_CLAIMS]
    chosen_ids = {id(c) for c in chosen}

    investigated, skipped = [], []
    for position, item in enumerate(items, start=1):
        claim = _build_claim(position, item)
        (investigated if id(item) in chosen_ids else skipped).append(claim)
    return ExtractionResult(investigated, skipped)
