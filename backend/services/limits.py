import asyncio
from dataclasses import dataclass

from config.settings import MAX_TAVILY_ATTEMPTS_PER_AUDIT


class Limits:
    """Per-provider concurrency limits, shared across all audits."""

    def __init__(self) -> None:
        self.tavily = asyncio.Semaphore(4)
        self.web_fetch = asyncio.Semaphore(5)
        self.llm = asyncio.Semaphore(2)


@dataclass
class AttemptBudget:
    """Counts every Tavily request, including retries, for one audit.

    Mandatory searches are held back from retries and gap searches, so those
    extras can only shrink the gap round.
    """

    limit: int = MAX_TAVILY_ATTEMPTS_PER_AUDIT
    used: int = 0
    mandatory_left: int = 0

    def plan_mandatory(self, count: int) -> None:
        self.mandatory_left = count

    def start_mandatory(self) -> bool:
        self.mandatory_left = max(0, self.mandatory_left - 1)
        if self.used >= self.limit:
            return False
        self.used += 1
        return True

    def skip_mandatory(self) -> None:
        self.mandatory_left = max(0, self.mandatory_left - 1)

    def take_extra(self) -> bool:
        if self.used + self.mandatory_left >= self.limit:
            return False
        self.used += 1
        return True
