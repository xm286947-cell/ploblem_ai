from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts import build_major_mvp_product_candidate as builder
from scripts.major_mvp_product_candidate_gate import verify_candidate


ROOT = Path(__file__).resolve().parents[1]


def test_candidate_source_selection_excludes_local_data_and_credentials() -> None:
    selected = {
        path.relative_to(ROOT).as_posix()
        for path in builder.source_files(ROOT)
    }

    assert "quality_knowledge/web/p0_app.py" in selected
    assert "quality_knowledge/web/static/major_production.js" in selected
    assert "services/historical_case_contract.py" in selected
    assert "input/new_cases.xlsx" not in selected
    assert "outputs/quality_capability_p1/plc_quality_issue_full_fields_e2e.xlsx" not in selected
    assert "knowledge/raw_excel/CASE-000001.json" not in selected
    assert "config/runtime/model.yaml" in selected  # Replaced with safe env-only profile at stage time.
    assert "config/model.yaml" in selected  # Replaced with safe disabled profile at stage time.
    assert not any("tests/" in path or path.endswith(".db") for path in selected)


def test_candidate_runtime_model_templates_have_no_literal_credentials() -> None:
    import re

    for text in (builder.SAFE_LEGACY_MODEL, builder.SAFE_RUNTIME_MODEL):
        assert "api_key_env:" in text
        assert re.search(r"(?im)^\s*api_key\s*:", text) is None
        assert re.search(r"(?im)^\s*secret\s*:", text) is None


def test_built_candidate_package_gate() -> None:
    archive = os.getenv("MAJOR_MVP_CANDIDATE_PATH", "").strip()
    if not archive:
        pytest.skip("Set MAJOR_MVP_CANDIDATE_PATH to run the final candidate package gate.")
    result = verify_candidate(archive)
    assert result["ZIP_EXISTS"] == "YES"
    assert result["SHA256_MATCH"] == "YES"
    assert result["MANIFEST_EXISTS"] == "YES"
    assert result["REAL_DB_INCLUDED"] == "NO"
    assert result["SECRET_INCLUDED"] == "NO"
    assert result["SOURCE_SHA_IN_MANIFEST"] == "CORRECT"
