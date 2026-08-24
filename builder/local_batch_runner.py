from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import shutil
import time
import traceback

import yaml

from builder.analysis_pipeline_runner import run_analysis_pipeline
from builder.m71_query_runner import run_m71_query
from builder.query_artifact_cleaner import clean_orphan_query_artifacts, clean_query_artifacts, raw_query_ids
from parser.common import write_json

BATCH_VERSION = "V2.4-M6"
DEFAULT_RUNS_DIR = "output/runs"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id() -> str:
    return datetime.now().strftime("RUN_%Y%m%d_%H%M%S")


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_copy(source: Path, target: Path) -> str:
    if not source.exists() or not source.is_file():
        return ""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return str(target)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _report_metrics(report: dict[str, Any]) -> dict[str, Any]:
    decision = report.get("repeat_decision") or {}
    summary = report.get("summary") or {}
    recommendation = report.get("recommendation") or {}
    return {
        "decision": decision.get("decision", ""),
        "confidence": decision.get("confidence", recommendation.get("confidence", 0)),
        "overall_similarity": recommendation.get("overall_similarity", summary.get("overall_similarity", 0)),
        "analysis_status": summary.get("analysis_status", ""),
    }


def _query_dir(run_root: Path, query_id: str) -> Path:
    return run_root / "queries" / query_id


def _snapshot_query_outputs(root: Path, run_root: Path, query_id: str, include_sensitive_debug: bool) -> dict[str, str]:
    qdir = _query_dir(run_root, query_id)
    artifacts = qdir / "artifacts"
    result: dict[str, str] = {}

    mappings = {
        "report_json": root / "output/reports" / query_id / "report.json",
        "report_markdown": root / "output/reports" / query_id / "report.md",
        "repeat_analysis": root / "knowledge/repeat_analysis" / query_id / "repeat_analysis.json",
    }
    for key, source in mappings.items():
        copied = _safe_copy(source, artifacts / source.name)
        if copied:
            result[key] = copied

    # Sensitive intermediate data is never copied unless explicitly requested.
    if include_sensitive_debug:
        sensitive = {
            "raw_query": root / "knowledge/raw_query" / f"{query_id}.json",
            "standard_query": root / "knowledge/standard_query" / f"{query_id}.json",
            "retrieval_result": root / "output/retrieval_results" / f"{query_id}.json",
        }
        for key, source in sensitive.items():
            copied = _safe_copy(source, qdir / "debug_sensitive" / source.name)
            if copied:
                result[key] = copied
    return result


def _write_summary_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# REPEAT_CASE 本地批量运行汇总",
        "",
        f"- Run ID：{summary.get('run_id', '')}",
        f"- Engine Version：{summary.get('engine_version', '')}",
        f"- 状态：{summary.get('status', '')}",
        f"- Query 数量：{summary.get('query_count', 0)}",
        f"- 成功：{summary.get('success_count', 0)}",
        f"- 失败：{summary.get('failed_count', 0)}",
        f"- 跳过：{summary.get('skipped_count', 0)}",
        f"- 成功率：{summary.get('success_rate', 0)}%",
        f"- 总耗时：{summary.get('elapsed_seconds', 0)} 秒",
        "",
        "## Query 结果",
        "",
        "| Query ID | 状态 | 失败阶段 | 耗时(秒) | 相似度 | 置信度 |",
        "|---|---|---|---:|---:|---:|",
    ]
    for item in summary.get("queries", []):
        lines.append(
            f"| {item.get('query_id','')} | {item.get('status','')} | {item.get('failed_stage','')} | "
            f"{item.get('elapsed_seconds',0)} | {item.get('overall_similarity',0)} | {item.get('confidence',0)} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_local_batch(
    project_root: str | Path,
    input_path: str | Path | None = None,
    run_id: str | None = None,
    resume: bool = False,
    retry_failed: bool = False,
    top_k: int | None = None,
    overwrite: bool = True,
    mock: bool = False,
    skip_ai: bool = False,
    include_sensitive_debug: bool = False,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    app = _load_yaml(root / "config/app.yaml")
    engine_version = str((app.get("app") or {}).get("version", BATCH_VERSION))
    runs_root = root / ((app.get("paths") or {}).get("runs_dir", DEFAULT_RUNS_DIR))
    current_run_id = run_id or _run_id()
    run_root = runs_root / current_run_id
    manifest_path = run_root / "run_manifest.json"
    previous_manifest = _read_json(manifest_path) if resume else {}

    if resume and not previous_manifest:
        raise FileNotFoundError(f"无法续跑，Run不存在或manifest缺失: {current_run_id}")
    if run_root.exists() and not resume:
        raise FileExistsError(f"Run目录已存在: {run_root}")

    run_root.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    manifest: dict[str, Any] = previous_manifest or {
        "run_id": current_run_id,
        "batch_version": BATCH_VERSION,
        "engine_version": engine_version,
        "started_at": _now(),
        "completed_at": "",
        "status": "RUNNING",
        "input": str(input_path or (root / "input/new_cases.xlsx")),
        "input_sha256": "",
        "query_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "queries": [],
        "privacy": {
            "real_data_local_only": True,
            "sensitive_debug_exported": include_sensitive_debug,
        },
    }
    manifest["status"] = "RUNNING"
    manifest["completed_at"] = ""
    write_json(manifest_path, manifest)

    parse_result: dict[str, Any] = {}
    if not resume:
        source = Path(input_path) if input_path else root / "input/new_cases.xlsx"
        if not source.is_absolute():
            source = root / source
        if source.exists():
            manifest["input_sha256"] = _sha256(source)
        parse_result = run_m71_query(root, input_path=str(source), overwrite=True)
        write_json(run_root / "parse_result.json", parse_result)
        if int(parse_result.get("failed", parse_result.get("failed_count", 0)) or 0) > 0:
            manifest["status"] = "FAILED"
            manifest["completed_at"] = _now()
            manifest["error"] = "QUERY_PARSE_FAILED"
            write_json(manifest_path, manifest)
            return manifest

    ids = raw_query_ids(root)
    clean_orphan_query_artifacts(root, ids)
    manifest["query_count"] = len(ids)

    previous_by_id = {item.get("query_id"): item for item in previous_manifest.get("queries", [])} if previous_manifest else {}
    query_results: list[dict[str, Any]] = []

    for query_id in ids:
        previous = previous_by_id.get(query_id)
        if resume and previous:
            should_run = previous.get("status") != "SUCCESS" if retry_failed else previous.get("status") not in {"SUCCESS", "SKIPPED"}
            if not should_run:
                item = dict(previous)
                item["status"] = "SKIPPED"
                item["skip_reason"] = "ALREADY_COMPLETED"
                query_results.append(item)
                continue

        q_started = time.perf_counter()
        qdir = _query_dir(run_root, query_id)
        qdir.mkdir(parents=True, exist_ok=True)
        clean_stats = clean_query_artifacts(root, [query_id])
        record: dict[str, Any] = {
            "query_id": query_id,
            "status": "RUNNING",
            "started_at": _now(),
            "completed_at": "",
            "elapsed_seconds": 0.0,
            "failed_stage": "",
            "error_type": "",
            "error": "",
            "cleaned_before_run": clean_stats,
            "overall_similarity": 0,
            "confidence": 0,
            "artifacts": {},
        }
        write_json(qdir / "status.json", record)
        try:
            pipeline = run_analysis_pipeline(
                root,
                query_id=query_id,
                from_stage="enrich",
                top_k=top_k,
                overwrite=overwrite,
                mock=mock,
                skip_ai=skip_ai,
            )
            write_json(qdir / "pipeline_result.json", pipeline)
            query_row = (pipeline.get("queries") or [{}])[0]
            record["status"] = "SUCCESS" if query_row.get("status") == "SUCCESS" else "FAILED"
            record["failed_stage"] = query_row.get("failed_stage", "")
            record["error"] = query_row.get("error", "")
            record["artifacts"] = _snapshot_query_outputs(root, run_root, query_id, include_sensitive_debug)
            report = _read_json(root / "output/reports" / query_id / "report.json")
            record.update(_report_metrics(report))
        except Exception as exc:
            record["status"] = "FAILED"
            record["error_type"] = type(exc).__name__
            record["error"] = str(exc)
            (qdir / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
        record["completed_at"] = _now()
        record["elapsed_seconds"] = round(time.perf_counter() - q_started, 3)
        write_json(qdir / "status.json", record)
        query_results.append(record)

        manifest["queries"] = query_results
        manifest["success_count"] = sum(1 for item in query_results if item.get("status") == "SUCCESS")
        manifest["failed_count"] = sum(1 for item in query_results if item.get("status") == "FAILED")
        manifest["skipped_count"] = sum(1 for item in query_results if item.get("status") == "SKIPPED")
        write_json(manifest_path, manifest)

    success = sum(1 for item in query_results if item.get("status") == "SUCCESS")
    failed = sum(1 for item in query_results if item.get("status") == "FAILED")
    skipped = sum(1 for item in query_results if item.get("status") == "SKIPPED")
    total = len(query_results)
    summary = {
        **manifest,
        "queries": query_results,
        "success_count": success,
        "failed_count": failed,
        "skipped_count": skipped,
        "success_rate": round((success / total * 100), 2) if total else 0.0,
        "status": "SUCCESS" if failed == 0 else ("PARTIAL_SUCCESS" if success else "FAILED"),
        "completed_at": _now(),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "failed_queries": [item.get("query_id") for item in query_results if item.get("status") == "FAILED"],
    }
    write_json(run_root / "summary.json", summary)
    write_json(run_root / "failed_queries.json", {"items": summary["failed_queries"]})
    _write_summary_markdown(run_root / "summary.md", summary)
    write_json(manifest_path, summary)
    return summary
