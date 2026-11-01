from typing import Any

from services.db import Repository


class Tracer:
    def __init__(self, repo: Repository):
        self.repo = repo

    async def emit(
        self,
        audit_id: str,
        stage: str,
        status: str,
        message: str | None = None,
        **metadata: Any,
    ) -> None:
        await self.repo.add_trace(audit_id, stage, status, message, metadata)
