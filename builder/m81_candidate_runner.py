from __future__ import annotations

from pathlib import Path
import time

from builder.candidate_loader import CandidateLoader
from builder.validators import validate_json
from parser.common import write_json
from repositories import JsonArtifactRepository
from services import KnowledgeService


def run_m81_load(root: Path, query_id: str | None = None, top_k: int | None = None, overwrite: bool = False) -> dict:
    root = root.resolve()
    candidate_dir = root / "output/candidate_cases"
    output_dir = root / "knowledge/analysis_context"
    schema_path = root / "schema/analysis_context.schema.json"
    files = [candidate_dir / f"{query_id}.json"] if query_id else sorted(candidate_dir.glob("*.json"))
    repository = JsonArtifactRepository(root)
    knowledge_service = KnowledgeService(repository)
    loader = CandidateLoader(root, knowledge_service=knowledge_service)
    started = time.perf_counter()
    summary = {
        "stage": "M8.1",
        "total_queries": 0,
        "total_candidates": 0,
        "success": 0,
        "partial": 0,
        "failed": 0,
        "existing_skipped": 0,
        "errors": [],
        "output_dir": str(output_dir),
        "elapsed_seconds": 0.0,
    }

    for candidate_file in files:
        if not candidate_file.exists():
            summary["failed"] += 1
            summary["errors"].append({"query_id": candidate_file.stem, "error": "CANDIDATE_FILE_NOT_FOUND"})
            continue
        summary["total_queries"] += 1
        current_query_id = candidate_file.stem
        try:
            payload = repository.load(candidate_file, required=True)
            assert payload is not None
            current_query_id = str(payload.get("query_id") or current_query_id)
            standard_query, retrieval_profile = knowledge_service.load_query_inputs(current_query_id)
            candidates = payload.get("results") or []
            if top_k is not None:
                candidates = candidates[:max(0, int(top_k))]
            summary["total_candidates"] += len(candidates)
            query_output_dir = output_dir / current_query_id
            query_output_dir.mkdir(parents=True, exist_ok=True)

            for candidate in candidates:
                case_id = str(candidate.get("case_id") or "UNKNOWN")
                target = query_output_dir / f"{case_id}.json"
                if target.exists() and not overwrite:
                    summary["existing_skipped"] += 1
                    continue
                try:
                    context = loader.load(current_query_id, candidate, standard_query, retrieval_profile, candidate_file)
                    errors = validate_json(context, schema_path)
                    if errors:
                        raise ValueError("ANALYSIS_CONTEXT_SCHEMA_INVALID: " + " | ".join(errors))
                    write_json(target, context)
                    summary["success"] += 1
                    if context["quality"]["status"] == "PARTIAL":
                        summary["partial"] += 1
                except Exception as exc:
                    summary["failed"] += 1
                    summary["errors"].append({"query_id": current_query_id, "case_id": case_id, "error": str(exc)})
        except Exception as exc:
            summary["failed"] += 1
            summary["errors"].append({"query_id": current_query_id, "error": str(exc)})

    summary["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    write_json(root / "output/logs/m81_candidate_loader_summary.json", summary)
    return summary
