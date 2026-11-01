from typing import Protocol

from schemas.search import AdapterResult, SearchRequest


class SearchAdapter(Protocol):
    name: str

    async def search(self, req: SearchRequest) -> AdapterResult: ...
