from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from tools.hardware_r1_wave4_execution import Wave4ExecutionError, _dataset, execute
import services.hardware_case_r1_workbench as workbench_module


def _docx(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    xml = b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>synthetic evidence</w:t></w:r></w:p></w:body></w:document>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)


def _fixture(tmp_path: Path) -> tuple[Path, Path, dict]:
    source_dir = tmp_path / "source"
    source = source_dir / "A1234-synthetic.docx"
    _docx(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = {
        "manifest_version": "hardware-r1-wave4-dataset/v1",
        "case_count": 1,
        "source_dir_recorded": False,
        "provider_calls": 0,
        "records": [{
            "source_id": digest, "file_size": source.stat().st_size,
            "business_case_id": "A1234-synthetic", "relative_name": source.name,
        }],
    }
    manifest_path = tmp_path / "DATASET_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    model_config = tmp_path / "model.yaml"
    model_config.write_text("active_model: fake\nmodels:\n  fake:\n    provider: fake\n    api_key_env: FAKE_PROVIDER_KEY\n", encoding="utf-8")
    config = {
        "source_dir": str(source_dir), "dataset_manifest": str(manifest_path),
        "validation_data_root": str(tmp_path / "validation"),
        "validation_bootstrap_path": str(tmp_path / "bootstrap" / "bootstrap.json"),
        "normal_product_data_root": str(tmp_path / "normal-product"),
        "output_dir": str(tmp_path / "output"), "model_config": str(model_config),
        "max_cases": 30, "allow_real_provider": False,
    }
    config_path = tmp_path / "execution.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path, source_dir, config


def _fake_pipeline(snapshot, structurer, **kwargs):
    source_id = snapshot["source"]["source_id"]
    case_id = snapshot["identity"]["business_case_id"]
    knowledge = {
        "contract_version": "hardware-case-knowledge-object/v1",
        "identity": {"business_case_id": case_id, "raw_title": "synthetic", "identity_status": "PARSED"},
        "source_fact": {"business_case_id": case_id, "source_id": source_id, "raw_title": "synthetic"},
        "engineering_context": {"primary_subject": {"value": "synthetic", "extraction_status": "EXTRACTED", "evidence_block_ids": ["B0001"]}},
        "evidence": [{"block_id": "B0001", "source_locator": {"paragraph": 1}, "text": "synthetic evidence"}],
        "conflicts": [{"conflict_id": "C1", "type": "TITLE_CONTENT_SUBJECT_MISMATCH", "field": "primary_subject", "source_values": [{"source": "SOURCE_RAW_TITLE", "value": "synthetic"}], "status": "OPEN", "resolution_status": "NEEDS_REVIEW"}],
        "review": {"object_status": "CANDIDATE", "reviewer": None, "reviewed_at": None, "field_decisions": []},
        "provenance": {"agent_config_version": "fake/v1"},
    }
    return {
        "pipeline_version": "fake-pipeline/v1", "knowledge_object_contract_version": "hardware-case-knowledge-object/v1",
        "pipeline_status": "GOLDEN_PREVIEW_READY", "status": "NEEDS_REVIEW", "failed_stage": None,
        "error_code": None, "provider_call_count": 0, "run_id": "fake-run-1", "duration_ms": 1,
        "runtime": {"stage_a": {"agent_config_version": "stage-a/v1", "provider_call_count": 0}, "stage_b": {"agent_config_version": "stage-b/v1", "provider_call_count": 0}},
        "evidence_validation": {"status": "PASS"}, "latency_trace": {}, "knowledge_object": knowledge,
    }


def test_execution_reuses_workbench_and_commits_durable_review_required_candidate(tmp_path, monkeypatch):
    config_path, _, config = _fixture(tmp_path)
    monkeypatch.setattr(workbench_module, "run_r1_agent_extraction", _fake_pipeline)
    result = execute(config_path, execute_real=False, structurer_factory=lambda: object())
    assert result["batch"]["status"] == "READY_FOR_REVIEW"
    item = result["batch"]["items"][0]
    assert item["candidate_id"]
    assert item["candidate_asset"]["production_review_status"] == "REQUIRED"
    assert item["candidate_asset"]["source_id"] == result["evidence"]["records"][0]["source_id"]
    assert result["summary"]["provider_call_total"] == 0
    assert (Path(config["validation_data_root"]) / "db/hardware_asset.db").is_file()
    assert not Path(config["normal_product_data_root"]).exists()
    sanitized = json.dumps(result["summary"])
    assert "A1234" not in sanitized
    assert str(tmp_path) not in sanitized


def test_retry_keeps_first_pass_immutable_and_requires_same_dataset_hash(tmp_path, monkeypatch):
    config_path, _, config = _fixture(tmp_path)
    monkeypatch.setattr(workbench_module, "run_r1_agent_extraction", _fake_pipeline)
    execute(config_path, execute_real=False, structurer_factory=lambda: object())
    first_report = Path(config["output_dir"]) / "WAVE4_EXECUTION_EVIDENCE.json"
    original = first_report.read_bytes()
    execute(config_path, execute_real=False, structurer_factory=lambda: object(), retry_failed=True)
    assert first_report.read_bytes() == original
    assert (Path(config["output_dir"]) / "WAVE4_RETRY_REPORT.json").is_file()
    manifest_path = Path(config["dataset_manifest"])
    manifest = json.loads(manifest_path.read_text())
    manifest["metadata_change"] = True
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(Wave4ExecutionError, match="RETRY_DATASET_HASH_MISMATCH"):
        execute(config_path, execute_real=False, structurer_factory=lambda: object(), retry_failed=True)


def test_two_factor_auth_and_inline_secret_fail_closed(tmp_path):
    config_path, _, config = _fixture(tmp_path)
    with pytest.raises(Wave4ExecutionError, match="REAL_PROVIDER_EXECUTION_NOT_AUTHORIZED"):
        execute(config_path, execute_real=True)
    config["allow_real_provider"] = True
    config["model_config"] = str(tmp_path / "inline.yaml")
    Path(config["model_config"]).write_text("active_model: fake\nmodels:\n  fake:\n    api_key: real-looking-secret\n", encoding="utf-8")
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(Wave4ExecutionError, match="INLINE_PROVIDER_SECRET_FORBIDDEN"):
        execute(config_path, execute_real=True)


def test_frozen_dataset_hash_and_duplicate_gates(tmp_path):
    config_path, source_dir, _ = _fixture(tmp_path)
    manifest_path = tmp_path / "DATASET_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["records"].append(dict(manifest["records"][0]))
    manifest["case_count"] = 2
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(Wave4ExecutionError, match="DUPLICATE_SOURCE_ID"):
        _dataset(manifest_path, source_dir, real_mode=False)
    manifest["records"] = manifest["records"][:1]
    manifest["case_count"] = 1
    manifest_path.write_text(json.dumps(manifest))
    (source_dir / "A1234-synthetic.docx").write_bytes(b"changed")
    with pytest.raises(Wave4ExecutionError, match="DATASET_SOURCE_MISMATCH"):
        _dataset(manifest_path, source_dir, real_mode=False)


def test_real_execution_count_gate(tmp_path):
    config_path, source_dir, _ = _fixture(tmp_path)
    with pytest.raises(Wave4ExecutionError, match="DATASET_COUNT_INVALID"):
        _dataset(tmp_path / "DATASET_MANIFEST.json", source_dir, real_mode=True)
