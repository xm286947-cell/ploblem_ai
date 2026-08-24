from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

import yaml

from parser.evidence_blocks import EvidenceBlockBuilder
from parser.pdf_extractor import PdfExtractor
from parser.section_classifier import ClassifiedBlock, SectionClassifier


class EvidenceParser:
    def __init__(self, project_root: str | Path) -> None:
        self.root = Path(project_root)
        pdf_config = yaml.safe_load((self.root / "config/pdf_parser.yaml").read_text(encoding="utf-8"))
        text_cfg = pdf_config.get("text", {})
        block_cfg = pdf_config.get("blocks", {})
        self.extractor = PdfExtractor(
            minimum_page_characters=int(text_cfg.get("minimum_page_characters", 20)),
            minimum_document_characters=int(text_cfg.get("minimum_document_characters", 80)),
        )
        self.block_builder = EvidenceBlockBuilder(
            max_block_characters=int(block_cfg.get("max_block_characters", 6000))
        )
        self.classifier = SectionClassifier(self.root / "config/section_mapping.yaml")

    def parse(self, pdf_path: str | Path) -> Dict[str, Any]:
        extraction = self.extractor.extract(pdf_path)
        blocks = self.block_builder.build(extraction)
        classified = [self.classifier.classify(block) for block in blocks]
        sections = self._merge_sections(classified)
        unclassified = [
            {
                "block_id": item.block.block_id,
                "page_ref": item.block.page_number,
                "content": item.content,
            }
            for item in classified
            if item.section_type == "unknown"
        ]
        warnings = list(extraction.warnings)
        for page in extraction.pages:
            warnings.extend(f"PAGE_{page.page_number}:{warning}" for warning in page.warnings)

        return {
            "report_file": str(pdf_path),
            "page_count": extraction.page_count,
            "total_characters": extraction.total_characters,
            "tables": extraction.tables,
            "sections": sections,
            "unclassified_blocks": unclassified,
            "parse_warnings": sorted(set(warnings)),
        }

    @staticmethod
    def _merge_sections(classified: List[ClassifiedBlock]) -> List[dict]:
        merged: List[dict] = []
        for item in classified:
            if item.section_type == "unknown":
                continue
            current = {
                "section_type": item.section_type,
                "title": item.title,
                "content": item.content,
                "page_refs": [item.block.page_number],
                "block_refs": [item.block.block_id],
                "confidence": item.confidence,
            }
            if (
                merged
                and merged[-1]["section_type"] == current["section_type"]
                and item.block.page_number - merged[-1]["page_refs"][-1] <= 1
            ):
                merged[-1]["content"] = (merged[-1]["content"] + "\n" + current["content"]).strip()
                merged[-1]["page_refs"] = sorted(set(merged[-1]["page_refs"] + current["page_refs"]))
                merged[-1]["block_refs"].extend(current["block_refs"])
                merged[-1]["confidence"] = round(
                    (merged[-1]["confidence"] + current["confidence"]) / 2, 4
                )
            else:
                merged.append(current)
        return merged
