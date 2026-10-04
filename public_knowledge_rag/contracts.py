from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    locators: tuple[str, ...]
    parser_id: str
    parser_version: str


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    text: str
    locator: str


@dataclass(frozen=True)
class SearchHit:
    hit_id: str
    source_id: str
    source_revision: str
    locator: str
    text: str
    score: float


class Parser(Protocol):
    parser_id: str
    parser_version: str

    def parse(self, content: bytes, media_type: str) -> ParsedDocument: ...


class Chunker(Protocol):
    def chunk(self, parsed: ParsedDocument) -> list[Chunk]: ...


class Retriever(Protocol):
    def search(self, query: str, top_k: int, source_ids: list[str] | None = None) -> list[SearchHit]: ...


class Index(Protocol):
    """Replaceable lexical or vector index boundary."""

    def search(self, query: str, top_k: int, source_ids: list[str] | None = None) -> list[SearchHit]: ...


class EmbeddingProvider(Protocol):
    provider_id: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class GenerationProvider(Protocol):
    provider_id: str

    def generate(self, question: str, contexts: list[SearchHit]) -> tuple[str, dict[str, object]]: ...


class ManualProviderAdapter(Protocol):
    """Slot for approved manual evaluation providers; not configured in this baseline."""

    provider_id: str

    def generate(self, prompt: str) -> str: ...


class CitationResolver(Protocol):
    def resolve(self, citation_id: str) -> dict[str, object] | None: ...
