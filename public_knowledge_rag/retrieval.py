from __future__ import annotations

from .contracts import SearchHit
from .store import Store


class SQLiteLexicalRetriever:
    """Replaceable reference adapter; callers depend on the Retriever contract."""

    adapter_id = "sqlite-lexical-reference"

    def __init__(self, store: Store) -> None:
        self.store = store

    def search(self, query: str, top_k: int, source_ids: list[str] | None = None) -> list[SearchHit]:
        return self.store.search(query, top_k, source_ids)
