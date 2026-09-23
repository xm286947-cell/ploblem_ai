from __future__ import annotations

import json
import shutil
from pathlib import Path

from builder.query_enricher import run_m72_pipeline
from builder.validators import validate_json

ROOT = Path(__file__).resolve().parents[1]


def _copy_project_inputs(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    for directory in ("config", "schema", "prompts"):
        shutil.copytree(ROOT / directory, root / directory)
    (root / "knowledge/raw_query").mkdir(parents=True)
    (root / "knowledge/normalized_query").mkdir(parents=True)
    (root / "knowledge/enriched_query").mkdir(parents=True)
    (root / "knowledge/standard_query").mkdir(parents=True)
    (root / "output/logs").mkdir(parents=True)
    shutil.copy2(ROOT / "knowledge/raw_query/QUERY001.json", root / "knowledge/raw_query/QUERY001.json")
    return root


def test_full_pipeline_generates_all_artifacts(tmp_path: Path) -> None:
    root = _copy_project_inputs(tmp_path)
    summary = run_m72_pipeline(root, query_id="QUERY001", overwrite=True, mock=True)
    assert summary["query_count"] == 1
    assert summary["failed"] == 0
    assert (root / "knowledge/normalized_query/QUERY001.json").exists()
    assert (root / "knowledge/enriched_query/QUERY001.json").exists()
    standard_path = root / "knowledge/standard_query/QUERY001.json"
    assert standard_path.exists()
    standard = json.loads(standard_path.read_text(encoding="utf-8"))
    assert validate_json(standard, root / "schema/standard_query.schema.json") == []


def test_skip_ai_degrades_but_builds(tmp_path: Path) -> None:
    root = _copy_project_inputs(tmp_path)
    summary = run_m72_pipeline(root, query_id="QUERY001", overwrite=True, skip_ai=True)
    assert summary["failed"] == 0
    enriched = json.loads((root / "knowledge/enriched_query/QUERY001.json").read_text(encoding="utf-8"))
    standard = json.loads((root / "knowledge/standard_query/QUERY001.json").read_text(encoding="utf-8"))
    assert enriched["enrich_status"] == "SKIPPED"
    assert "AI_ENRICHMENT_UNAVAILABLE" in standard["quality_flags"]


def test_rerun_from_build_uses_existing_enriched(tmp_path: Path) -> None:
    root = _copy_project_inputs(tmp_path)
    run_m72_pipeline(root, query_id="QUERY001", overwrite=True, mock=True)
    target = root / "knowledge/standard_query/QUERY001.json"
    target.unlink()
    summary = run_m72_pipeline(root, query_id="QUERY001", overwrite=True, from_stage="build")
    assert summary["failed"] == 0
    assert target.exists()
    assert list(summary["stages"]) == ["build"]


def test_missing_query_is_isolated_and_reported(tmp_path: Path) -> None:
    root = _copy_project_inputs(tmp_path)
    summary = run_m72_pipeline(root, query_id="NOT_FOUND", overwrite=True, mock=True)
    assert summary["failed"] == 1
    assert summary["queries"][0]["standard_query_generated"] is False
    assert summary["stages"]["normalize"]["failed"] == 1
