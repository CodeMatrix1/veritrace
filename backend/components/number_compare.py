import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from schemas.claim import NumericFact

MULTIPLIERS = {
    "k": 3, "thousand": 3, "lakh": 5, "lakhs": 5, "lac": 5, "lacs": 5,
    "million": 6, "crore": 7, "crores": 7, "cr": 7,
    "billion": 9, "trillion": 12,
}
CURRENCIES = {
    "₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR",
    "$": "USD", "usd": "USD", "€": "EUR", "£": "GBP",
}
COMPARATORS = {
    "exactly": "exact", "precisely": "exact",
    "about": "approx", "around": "approx", "nearly": "approx",
    "approximately": "approx", "roughly": "approx", "almost": "approx",
    "over": "over", "more than": "over", "above": "over", "greater than": "over",
    "under": "under", "less than": "under", "below": "under", "fewer than": "under",
}

NUMBER_RE = re.compile(
    r"""
    (?P<comparator>exactly|precisely|about|around|nearly|approximately|roughly|almost|
       over|more\s+than|above|greater\s+than|under|less\s+than|below|fewer\s+than)?\s*
    (?P<currency>₹|\$|€|£|rs\.?(?=\s*\d)|inr(?=\s*\d)|usd(?=\s*\d))?\s*
    (?P<number>(?<![\w.,-])\d+(?:,\d+)*(?:\.\d+)?)
    (?:\s*(?P<scale>k|thousand|lakhs?|lacs?|million|crores?|cr|billion|trillion)\b)?
    (?:\s*(?P<unit>%|percentage\s+points?|percent\b|per\s+cent\b))?
    """,
    re.IGNORECASE | re.VERBOSE,
)
YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def parse_numbers(text: str) -> list[NumericFact]:
    facts = []
    for match in NUMBER_RE.finditer(text):
        raw = match["number"]
        following = text[match.end("number"):match.end("number") + 3]
        if (
            YEAR_RE.match(raw)
            and not any([match["currency"], match["scale"], match["unit"]])
        ):
            continue
        if re.match(r"(st|nd|rd|th)\b", following, re.IGNORECASE):
            continue
        fact = _to_fact(match)
        if fact is not None:
            facts.append(fact)
    return facts


def _to_fact(match: re.Match) -> NumericFact | None:
    digits = match["number"].replace(",", "")
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    shift = MULTIPLIERS.get((match["scale"] or "").lower(), 0)
    value = value.scaleb(shift)

    unit = None
    unit_text = (match["unit"] or "").lower()
    if unit_text:
        unit = "percentage_point" if "point" in unit_text else "percent"
    elif match["currency"]:
        unit = CURRENCIES[match["currency"].lower()]

    comparator_text = " ".join((match["comparator"] or "").lower().split())
    return NumericFact(
        value=value,
        unit=unit,
        precision=decimals - shift,
        comparator=COMPARATORS.get(comparator_text, "approx"),
        original_text=match.group(0).strip(),
    )


def rounds_to_claim(source: Decimal, claim: Decimal, precision: int) -> bool:
    step = Decimal(1).scaleb(-precision)
    return source.quantize(step, rounding=ROUND_HALF_UP) == claim


def compare(claim: NumericFact, source: NumericFact) -> str:
    if claim.unit != source.unit:
        return "different_unit"
    if claim.comparator == "over":
        return "match" if source.value > claim.value else "mismatch"
    if claim.comparator == "under":
        return "match" if source.value < claim.value else "mismatch"
    if source.value == claim.value:
        return "exact"
    if claim.comparator == "approx" and rounds_to_claim(
        source.value, claim.value, claim.precision
    ):
        return "rounded"
    return "mismatch"


def best_comparison(claim: NumericFact, sources: list[NumericFact]) -> str:
    """Return the strongest outcome for one claim number against a passage."""
    outcomes = {compare(claim, s) for s in sources}
    for outcome in ("exact", "match", "rounded", "mismatch"):
        if outcome in outcomes:
            return outcome
    return "different_unit"
