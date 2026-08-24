from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class EvidenceSection:
    section_type: str
    title: str = ""
    content: str = ""
    page_refs: List[int] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class EvidenceResult:
    report_file: str
    sections: List[EvidenceSection] = field(default_factory=list)
    unclassified_blocks: List[str] = field(default_factory=list)
    parse_warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "report_file": self.report_file,
            "sections": [
                {
                    "section_type": item.section_type,
                    "title": item.title,
                    "content": item.content,
                    "page_refs": item.page_refs,
                    "confidence": item.confidence,
                }
                for item in self.sections
            ],
            "unclassified_blocks": self.unclassified_blocks,
            "parse_warnings": self.parse_warnings,
        }
