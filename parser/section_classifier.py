from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple
import re
import unicodedata

import yaml

from parser.evidence_blocks import EvidenceBlock


@dataclass
class ClassifiedBlock:
    block: EvidenceBlock
    section_type: str
    title: str
    content: str
    confidence: float


class SectionClassifier:
    def __init__(self, config_path: str | Path) -> None:
        config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        self.section_titles: Dict[str, List[str]] = {
            section_type: values.get("titles", [])
            for section_type, values in config["section_types"].items()
        }
        classifier = config.get("classifier", {})
        self.minimum_score = float(classifier.get("minimum_score", 0.70))
        self.max_title_length = int(classifier.get("max_title_length", 40))

    @staticmethod
    def _norm(value: str) -> str:
        value = unicodedata.normalize("NFKC", value or "").strip().lower()
        return re.sub(r"[\s:：\-—_()（）【】\[\]]+", "", value)

    def _detect_title(self, block_text: str) -> Tuple[str, str]:
        lines = [line.strip() for line in block_text.splitlines() if line.strip()]
        if not lines:
            return "", ""
        first = lines[0]
        if len(first) <= self.max_title_length:
            return first, "\n".join(lines[1:]).strip()
        # Also support "标题：正文" on one line.
        match = re.match(r"^(.{1,30}?)[：:]\s*(.+)$", first)
        if match:
            remainder = "\n".join([match.group(2)] + lines[1:]).strip()
            return match.group(1).strip(), remainder
        return "", block_text.strip()

    def classify(self, block: EvidenceBlock) -> ClassifiedBlock:
        title, body = self._detect_title(block.content)
        normalized_title = self._norm(title)
        best_type = "unknown"
        best_score = 0.0

        for section_type, aliases in self.section_titles.items():
            for alias in aliases:
                normalized_alias = self._norm(alias)
                if not normalized_alias or not normalized_title:
                    continue
                if normalized_title == normalized_alias:
                    score = 1.0
                elif normalized_title.startswith(normalized_alias):
                    score = 0.95
                elif normalized_alias in normalized_title:
                    score = 0.85
                else:
                    score = 0.0
                if score > best_score:
                    best_type, best_score = section_type, score

        if best_score < self.minimum_score:
            return ClassifiedBlock(block, "unknown", title, block.content.strip(), 0.0)
        return ClassifiedBlock(block, best_type, title, body or block.content.strip(), best_score)
