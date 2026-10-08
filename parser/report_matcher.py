from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import yaml

from parser.common import file_sha256, normalize_filename, write_json


class ReportMatcher:
    def __init__(self, config_path: str | Path) -> None:
        config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        matching = config["matching"]
        self.extensions = {ext.lower() for ext in matching.get("extensions", [".pdf", ".docx"])}
        self.recursive = bool(matching.get("recursive", True))

    def _scan(self, reports_dir: Path) -> List[Path]:
        iterator = reports_dir.rglob("*") if self.recursive else reports_dir.glob("*")
        return sorted(
            path.resolve()
            for path in iterator
            if path.is_file() and path.suffix.lower() in self.extensions
        )

    def _build_indexes(self, files: List[Path]) -> tuple[Dict[str, List[Path]], Dict[str, List[Path]]]:
        by_exact_name: Dict[str, List[Path]] = {}
        by_normalized_name: Dict[str, List[Path]] = {}
        for path in files:
            by_exact_name.setdefault(path.name, []).append(path)
            by_normalized_name.setdefault(normalize_filename(path.name), []).append(path)
        return by_exact_name, by_normalized_name

    def match_one(self, record: dict, reports_dir: str | Path) -> dict:
        reports_path = Path(reports_dir)
        files = self._scan(reports_path)
        by_exact_name, by_normalized_name = self._build_indexes(files)

        case_id = record["case_id"]
        fields = record.get("mapped_fields", {})
        report_filename = str(fields.get("report_filename", "") or "").strip()
        warnings: List[str] = []
        candidates: List[Path] = []
        match_type = "NOT_FOUND"

        if not report_filename:
            match_type = "NO_REPORT_NAME"
        else:
            basename = Path(report_filename).name
            normalized_name = normalize_filename(basename)
            normalized_candidates = by_normalized_name.get(normalized_name, [])
            exact_candidates = by_exact_name.get(basename, [])
            # Normalized collisions are ambiguity, even when one spelling is an
            # exact match. Never guess between case/width/whitespace variants.
            if len(normalized_candidates) > 1:
                candidates = normalized_candidates
                match_type = "AMBIGUOUS"
            elif exact_candidates:
                candidates = exact_candidates
                match_type = "EXACT_FILENAME"
            elif normalized_candidates:
                candidates = normalized_candidates
                match_type = "NORMALIZED_FILENAME"

        # Deduplicate
        candidates = sorted(set(candidates))
        matched = ""
        parse_status = "REPORT_NOT_FOUND"
        file_hash = ""
        file_size = 0

        if match_type == "NO_REPORT_NAME":
            parse_status = "NO_REPORT"
            warnings.append("REPORT_FILENAME_EMPTY")
        elif len(candidates) == 0:
            match_type = "NOT_FOUND"
            parse_status = "REPORT_NOT_FOUND"
            warnings.append("REPORT_NOT_FOUND")
        elif len(candidates) > 1:
            match_type = "AMBIGUOUS"
            parse_status = "AMBIGUOUS"
            warnings.append("MULTIPLE_REPORT_MATCHES")
        else:
            selected = candidates[0]
            matched = str(selected)
            parse_status = "NOT_PARSED"
            file_hash = file_sha256(selected)
            file_size = selected.stat().st_size

        status = {
            "EXACT_FILENAME": "MATCHED",
            "NORMALIZED_FILENAME": "MATCHED",
            "NOT_FOUND": "NOT_FOUND",
            "AMBIGUOUS": "AMBIGUOUS",
            "NO_REPORT_NAME": "REVIEW_REQUIRED",
        }.get(match_type, "REVIEW_REQUIRED")
        return {
            "case_id": case_id,
            "report_filename": report_filename,
            "matched_report_path": matched,
            "candidate_paths": [str(path) for path in candidates],
            "match_type": match_type,
            "match_status": status,
            "file_hash": file_hash,
            "file_size": file_size,
            "parse_status": parse_status,
            "sections": [],
            "unclassified_blocks": [],
            "parse_warnings": warnings,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def match_all(
        self,
        records: List[dict],
        reports_dir: str | Path,
        output_dir: str | Path,
    ) -> tuple[List[dict], dict]:
        results: List[dict] = []
        counts: Dict[str, int] = {}
        for record in records:
            result = self.match_one(record, reports_dir)
            write_json(Path(output_dir) / f"{record['case_id']}.json", result)
            results.append(result)
            counts[result["match_type"]] = counts.get(result["match_type"], 0) + 1

        summary = {
            "reports_dir": str(reports_dir),
            "total_cases": len(records),
            "match_type_counts": counts,
            "matched_count": sum(
                count for name, count in counts.items()
                if name in {"EXACT_FILENAME", "NORMALIZED_FILENAME"}
            ),
            "unmatched_count": counts.get("NO_REPORT_NAME", 0) + counts.get("NOT_FOUND", 0),
            "ambiguous_count": counts.get("AMBIGUOUS", 0),
            "status_counts": {
                "MATCHED": sum(counts.get(name, 0) for name in {"EXACT_FILENAME", "NORMALIZED_FILENAME"}),
                "NOT_FOUND": counts.get("NOT_FOUND", 0),
                "AMBIGUOUS": counts.get("AMBIGUOUS", 0),
                "REVIEW_REQUIRED": counts.get("NO_REPORT_NAME", 0),
            },
        }
        return results, summary
