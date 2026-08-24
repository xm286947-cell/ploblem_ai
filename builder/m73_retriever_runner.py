from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import yaml

from parser.common import write_json
from retriever.case_retriever import CaseRetriever
from retriever.profile_adapter import profile_to_query_input


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def run_m73_retrieve(root: Path, query_id: str | None = None, top_k: int | None = None, overwrite: bool = False) -> dict:
    profile_dir = root / "knowledge/retrieval_profile"
    output_dir = root / "output/candidate_cases"
    output_dir.mkdir(parents=True, exist_ok=True)
    files = [profile_dir / f"{query_id}.json"] if query_id else sorted(profile_dir.glob("*.json"))
    retriever = CaseRetriever(root, _load_yaml(root / "config/app.yaml"), _load_yaml(root / "config/model.yaml"), _load_yaml(root / "config/retrieval.yaml"))
    summary = {"stage": "M7.3.3", "total": 0, "success": 0, "failed": 0, "existing_skipped": 0, "returned_candidates": 0, "errors": [], "output_dir": str(output_dir), "elapsed_seconds": 0.0}
    started = time.perf_counter()
    for source in files:
        if not source.exists():
            summary["failed"] += 1
            summary["errors"].append({"file": str(source), "error": "RETRIEVAL_PROFILE_NOT_FOUND"})
            continue
        target = output_dir / source.name
        if target.exists() and not overwrite:
            summary["existing_skipped"] += 1
            continue
        summary["total"] += 1
        try:
            profile = json.loads(source.read_text(encoding="utf-8"))
            query = profile_to_query_input(profile)
            result = retriever.search(query, top_k=top_k)
            result["query_id"] = profile["query_id"]
            result["source_retrieval_profile"] = str(source.relative_to(root))
            result["retrieval_profile_version"] = profile["metadata"]["profile_config_version"]
            result["adapter_version"] = "M7.3-A1"
            write_json(target, result)
            summary["success"] += 1
            summary["returned_candidates"] += int(result.get("returned_count", 0))
        except Exception as exc:
            summary["failed"] += 1
            summary["errors"].append({"file": str(source), "error": str(exc)})
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    write_json(root / "output/logs/m73_retriever_summary.json", summary)
    return summary
