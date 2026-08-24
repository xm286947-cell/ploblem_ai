from __future__ import annotations

from pathlib import Path
import shutil
from typing import Iterable

# Query-scoped artifacts only. Product/case knowledge is intentionally excluded.
_FILE_DIRS = (
    "knowledge/normalized_query",
    "knowledge/enriched_query",
    "knowledge/standard_query",
    "knowledge/retrieval_profile",
    "output/candidate_cases",
    "output/retrieval_results",
)
_TREE_DIRS = (
    "knowledge/analysis_context",
    "knowledge/similarity_analysis",
    "knowledge/solution_analysis",
    "knowledge/repeat_analysis",
)
_REPORT_ROOTS = ("output/reports",)


def raw_query_ids(root: Path) -> list[str]:
    return sorted(path.stem for path in (root / "knowledge/raw_query").glob("*.json"))


def clean_query_artifacts(root: Path, query_ids: Iterable[str]) -> dict[str, int]:
    ids = {str(item).strip() for item in query_ids if str(item).strip()}
    deleted_files = 0
    deleted_dirs = 0
    for query_id in ids:
        for relative in _FILE_DIRS:
            target = root / relative / f"{query_id}.json"
            if target.exists():
                target.unlink()
                deleted_files += 1
        for relative in _TREE_DIRS:
            target = root / relative / query_id
            if target.exists():
                shutil.rmtree(target)
                deleted_dirs += 1
        for relative in _REPORT_ROOTS:
            report_root = root / relative
            if not report_root.exists():
                continue
            # Current reports are archived by run_id/query_id. Remove only this query's folders.
            for target in report_root.glob(f"*/{query_id}"):
                if target.is_dir():
                    shutil.rmtree(target)
                    deleted_dirs += 1
    return {"deleted_files": deleted_files, "deleted_dirs": deleted_dirs}


def clean_orphan_query_artifacts(root: Path, valid_query_ids: Iterable[str]) -> dict[str, int]:
    valid = {str(item).strip() for item in valid_query_ids if str(item).strip()}
    orphan_ids: set[str] = set()
    for relative in _FILE_DIRS:
        directory = root / relative
        if directory.exists():
            orphan_ids.update(path.stem for path in directory.glob("*.json") if path.stem not in valid)
    for relative in _TREE_DIRS:
        directory = root / relative
        if directory.exists():
            orphan_ids.update(path.name for path in directory.iterdir() if path.is_dir() and path.name not in valid)
    result = clean_query_artifacts(root, orphan_ids)
    result["orphan_queries"] = len(orphan_ids)
    return result
