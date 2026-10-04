from __future__ import annotations

from .contracts import Chunk, ParsedDocument


class PlainTextParser:
    """Bootstrap parser for text input; replaceable through the Parser contract."""

    parser_id = "plain-text"
    parser_version = "1"

    def parse(self, content: bytes, media_type: str) -> ParsedDocument:
        if media_type.split(";", 1)[0].strip().lower() not in {"text/plain", "text/markdown"}:
            raise ValueError("This infrastructure baseline accepts text/plain or text/markdown only.")
        text = content.decode("utf-8-sig").strip()
        if not text:
            raise ValueError("Source content is empty.")
        return ParsedDocument(text=text, locators=("text:1",), parser_id=self.parser_id, parser_version=self.parser_version)


class WindowChunker:
    """Deterministic, parameterized chunker behind the replaceable Chunker contract."""

    def __init__(self, chunk_size: int = 1200, overlap: int = 120) -> None:
        if chunk_size < 100 or overlap < 0 or overlap >= chunk_size:
            raise ValueError("Invalid chunk size or overlap.")
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, parsed: ParsedDocument) -> list[Chunk]:
        text = parsed.text
        chunks: list[Chunk] = []
        start = 0
        while start < len(text):
            end = min(len(text), start + self.chunk_size)
            if end < len(text):
                boundary = text.rfind("\n", start + self.chunk_size // 2, end)
                if boundary > start:
                    end = boundary
            chunks.append(Chunk(len(chunks), text[start:end].strip(), f"char:{start}-{end}"))
            if end >= len(text):
                break
            start = max(start + 1, end - self.overlap)
        return chunks
