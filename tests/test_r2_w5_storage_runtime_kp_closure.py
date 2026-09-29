from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

from products.storage_rc1.storage_life import knowledge_product, runtime_bridge


ROOT = Path(__file__).resolve().parents[1]


def _load_windows_launcher():
    path = ROOT / "scripts" / "overall_r2_windows_start.py"
    spec = importlib.util.spec_from_file_location("overall_r2_windows_start", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_storage_runtime_accepts_exact_overall_r2_source_binding(tmp_path: Path):
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "config" / "runtime").mkdir(parents=True)
    (tmp_path / "config" / "runtime" / "model.yaml").write_text(
        "active_model: qwen_prod\nmodels: {}\n",
        encoding="utf-8",
    )
    (tmp_path / "tools" / "openai_mock").mkdir(parents=True)
    (tmp_path / "tools" / "openai_mock" / "server.py").write_text(
        "# packaged runtime probe\n",
        encoding="utf-8",
    )
    commit = "1" * 40
    (tmp_path / "R2_SOURCE_COMMIT").write_text(commit + "\n", encoding="utf-8")
    (tmp_path / "OVERALL_R2_RELEASE_CANDIDATE_MANIFEST.json").write_text(
        json.dumps(
            {
                "contract": "overall-r2-release-candidate/v1",
                "dut_source_commit": commit,
            }
        ),
        encoding="utf-8",
    )

    status = runtime_bridge._verify_runtime_root(tmp_path)

    assert status["pinned"] is True
    assert status["source_binding"] == "OVERALL_R2_SOURCE_COMMIT"
    assert status["source_commit"] == commit


def test_storage_runtime_rejects_mismatched_overall_r2_source_binding(tmp_path: Path):
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "config" / "runtime").mkdir(parents=True)
    (tmp_path / "config" / "runtime" / "model.yaml").write_text(
        "active_model: qwen_prod\nmodels: {}\n",
        encoding="utf-8",
    )
    (tmp_path / "tools" / "openai_mock").mkdir(parents=True)
    (tmp_path / "tools" / "openai_mock" / "server.py").write_text(
        "# packaged runtime probe\n",
        encoding="utf-8",
    )
    (tmp_path / "R2_SOURCE_COMMIT").write_text("1" * 40, encoding="utf-8")
    (tmp_path / "OVERALL_R2_RELEASE_CANDIDATE_MANIFEST.json").write_text(
        json.dumps(
            {
                "contract": "overall-r2-release-candidate/v1",
                "dut_source_commit": "2" * 40,
            }
        ),
        encoding="utf-8",
    )

    try:
        runtime_bridge._verify_runtime_root(tmp_path)
    except runtime_bridge.RuntimeBridgeUnavailable as exc:
        assert "source mismatch" in str(exc)
    else:
        raise AssertionError("mismatched Overall R2 source binding must fail closed")


def test_storage_knowledge_uses_shared_model_config_and_external_state(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.delenv("STORAGE_MODEL_CONFIG", raising=False)
    assert knowledge_product.model_config_path() == (
        ROOT / "config" / "runtime" / "model.yaml"
    ).resolve()

    repository = tmp_path / "knowledge-repository"
    release = tmp_path / "knowledge-release" / "current"
    monkeypatch.setenv("STORAGE_KNOWLEDGE_REPOSITORY_DIR", str(repository))
    monkeypatch.setenv("STORAGE_KNOWLEDGE_RELEASE_DIR", str(release))

    assert knowledge_product.repository_root() == repository.resolve()
    assert knowledge_product.active_release_dir() == release.resolve()
    assert repository.is_dir()


def test_overall_windows_launcher_binds_single_runtime_and_persistent_knowledge(
    monkeypatch,
    tmp_path: Path,
):
    launcher = _load_windows_launcher()
    package_root = tmp_path / "package"
    (package_root / "config" / "runtime").mkdir(parents=True)
    (package_root / "config" / "runtime" / "model.yaml").write_text(
        "active_model: qwen_prod\nmodels: {}\n",
        encoding="utf-8",
    )
    bindings = {
        "legacy_db": tmp_path / "legacy.db",
        "storage_data": tmp_path / "data" / "storage",
        "storage_runtime_db": tmp_path / "data" / "runtime" / "storage_runtime.sqlite3",
        "knowledge_repository": tmp_path / "data" / "knowledge_repository",
        "knowledge_release": tmp_path / "data" / "knowledge_release" / "current",
    }

    monkeypatch.delenv("HARDWARE_CASE_HOST_ROLE", raising=False)
    env = launcher.build_process_env(package_root.resolve(), bindings)

    assert env["HARDWARE_CASE_HOST_ROLE"] == "CONSUMER"
    assert env["STORAGE_LIFE_EXECUTION_MODE"] == "runtime"
    assert Path(env["UNIFIED_AGENT_RUNTIME_ROOT"]) == package_root.resolve()
    assert Path(env["STORAGE_MODEL_CONFIG"]) == (
        package_root / "config" / "runtime" / "model.yaml"
    ).resolve()
    assert Path(env["STORAGE_LIFE_RUNTIME_DB"]) == bindings["storage_runtime_db"]
    assert Path(env["STORAGE_KNOWLEDGE_REPOSITORY_DIR"]) == bindings["knowledge_repository"]
    assert Path(env["STORAGE_KNOWLEDGE_RELEASE_DIR"]) == bindings["knowledge_release"]


def test_storage_knowledge_ui_reports_loading_success_and_failure_in_place():
    html = (
        ROOT
        / "products"
        / "storage_rc1"
        / "storage_life"
        / "index.html"
    ).read_text(encoding="utf-8")

    assert "SourceDocument 导入中" in html
    assert "SourceDocument 导入失败" in html
    assert "AI Extraction 执行中" in html
    assert "AI Extraction 失败" in html
    assert "知识资料刷新失败" in html
