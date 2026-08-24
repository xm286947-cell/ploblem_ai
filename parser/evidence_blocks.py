from __future__ import annotations

from dataclasses import dataclass
from typing import List
import re

from parser.pdf_extractor import PdfExtractionResult


@dataclass
class EvidenceBlock:
    block_id: str
    page_number: int
    order_in_page: int
    content: str


class EvidenceBlockBuilder:
    def __init__(self, max_block_characters: int = 6000) -> None:
        self.max_block_characters = max_block_characters

    def build(self, extraction: PdfExtractionResult) -> List[EvidenceBlock]:
        blocks: List[EvidenceBlock] = []
        for page in extraction.pages:
            if not page.text:
                continue
            chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", page.text) if chunk.strip()]
            if not chunks:
                chunks = [line.strip() for line in page.text.splitlines() if line.strip()]

            order = 0
            for chunk in chunks:
                for start in range(0, len(chunk), self.max_block_characters):
                    content = chunk[start:start + self.max_block_characters].strip()
                    if not content:
                        continue
                    order += 1
                    blocks.append(EvidenceBlock(
                        block_id=f"P{page.page_number:04d}-B{order:04d}",
                        page_number=page.page_number,
                        order_in_page=order,
                        content=content,
                    ))
        return blocks
