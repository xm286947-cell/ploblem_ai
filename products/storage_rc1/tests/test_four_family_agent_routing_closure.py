from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from knowledge_production import KnowledgeExtractionService
from storage_life import ai, runtime_bridge
from storage_life.app import app


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("device_type", "agent_id", "dedicated"),
    [
        ("NOR Flash", runtime_bridge.GENERIC_AGENT_ID, False),
        ("NAND Flash", runtime_bridge.GENERIC_AGENT_ID, False),
        ("eMMC", runtime_bridge.EMMC_PARAMETER_AGENT_ID, True),
        ("SSD", runtime_bridge.GENERIC_AGENT_ID, False),
    ],
)
def test_four_family_routing_contract(device_type, agent_id, dedicated):
    route = runtime_bridge.route_for_device_type(device_type)
    assert route["device_type"] == device_type
    assert route["agent_id"] == agent_id
    assert route["dedicated_agent"] is dedicated
    assert Path(route["agent_config_path"]).is_file()
    if device_type == "eMMC":
        assert route["business_contract"] == "StorageParameterExtractResultV1"
    else:
        assert route["business_contract"] == "StorageDynamicJson"


def test_canonical_agent_config_directory_is_single_effective_storage_source():
    canonical = ROOT / "config" / "runtime" / "agents"
    assert runtime_bridge.agent_config_dir() == canonical.resolve()
    assert (canonical / "storage.ai.json_call.yaml").is_file()
    assert (canonical / "storage.emmc.parameter_extract.yaml").is_file()
    # Knowledge Production is shared-owned; Storage does not keep a divergent source copy.
    assert not (canonical / "knowledge.production.extract.yaml").exists()
    assert runtime_bridge.knowledge_production_agent_config_path().is_file()

    # Old product-local duplicate paths are intentionally removed.
    assert not (ROOT / "config" / "runtime" / "storage.ai.json_call.yaml").exists()
    assert not (ROOT / "config" / "runtime" / "storage.emmc.parameter_extract.yaml").exists()


def test_four_family_configs_preserve_device_specific_scope():
    generic = yaml.safe_load(
        (ROOT / "config" / "runtime" / "agents" / "storage.ai.json_call.yaml").read_text(encoding="utf-8")
    )
    emmc = yaml.safe_load(
        (ROOT / "config" / "runtime" / "agents" / "storage.emmc.parameter_extract.yaml").read_text(encoding="utf-8")
    )
    assert generic["metadata"]["routing_scope"] == ["NOR Flash", "NAND Flash", "SSD"]
    assert "eMMC" not in generic["metadata"]["routing_scope"]
    assert emmc["metadata"]["routing_scope"] == ["eMMC"]
    assert emmc["output_schema"]["ref"] == "StorageParameterExtractResultV1"
    assert generic["output_schema"]["ref"] == "StorageDynamicJson"


def test_parameter_extraction_dispatch_never_routes_other_families_to_emmc(monkeypatch):
    monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
    calls = []

    def generic(*_args, **_kwargs):
        calls.append(runtime_bridge.GENERIC_AGENT_ID)
        return {"fields": []}

    def emmc(*_args, **_kwargs):
        calls.append(runtime_bridge.EMMC_PARAMETER_AGENT_ID)
        return {"fields": []}

    monkeypatch.setattr(runtime_bridge, "call_json", generic)
    monkeypatch.setattr(runtime_bridge, "call_parameter_extract", emmc)

    for device_type in ("NOR Flash", "NAND Flash", "SSD", "eMMC"):
        calls.clear()
        result, secondary, model_calls = ai._primary_or_secondary_extraction(
            "instructions",
            {"device_type": device_type},
            {"type": "object"},
            device_type=device_type,
            vendor="Vendor",
            product_family="Family",
            field_map={},
        )
        assert result == {"fields": []}
        assert secondary is None
        assert model_calls == 1
        expected = (
            runtime_bridge.EMMC_PARAMETER_AGENT_ID
            if device_type == "eMMC"
            else runtime_bridge.GENERIC_AGENT_ID
        )
        assert calls == [expected]


def test_knowledge_production_remains_generic_not_emmc():
    boundary = runtime_bridge.knowledge_production_boundary()
    assert boundary["agent_id"] == "knowledge.production.extract"
    assert boundary["emmc_only"] is False
    assert KnowledgeExtractionService.AGENT_ID == "knowledge.production.extract"
    assert Path(boundary["agent_config_path"]).is_file()
    assert Path(boundary["agent_config_path"]) == runtime_bridge.knowledge_production_agent_config_path()


def test_runtime_status_exposes_routing_even_when_runtime_is_not_available(monkeypatch):
    monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
    monkeypatch.setattr(runtime_bridge, "runtime_root", lambda: None)
    runtime_bridge.reset_for_tests()
    status = runtime_bridge.status("SSD")
    assert status["execution_mode"] == "runtime"
    assert status["execution_mode_source"] == "environment"
    assert status["active_route"]["agent_id"] == runtime_bridge.GENERIC_AGENT_ID
    assert set(status["routing"]) == {"NOR Flash", "NAND Flash", "eMMC", "SSD"}
    assert status["legacy_config_policy"]["runtime_reads_legacy_config"] is False
    assert "error" in status


def test_runtime_route_api_exposes_effective_route_without_secret(monkeypatch):
    monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
    monkeypatch.setattr(runtime_bridge, "runtime_root", lambda: None)
    runtime_bridge.reset_for_tests()
    client = TestClient(app)
    response = client.get("/api/v1/runtime/route", params={"device_type": "NAND Flash"})
    assert response.status_code == 200
    data = response.json()
    assert data["agent_id"] == runtime_bridge.GENERIC_AGENT_ID
    assert data["device_type"] == "NAND Flash"
    rendered = json.dumps(data, ensure_ascii=False)
    assert "api_key" not in rendered.lower()
    assert "authorization" not in rendered.lower()


def test_release_launchers_are_runtime_explicit_and_do_not_exec_replace_shell():
    local = (ROOT / "run_local.sh").read_text(encoding="utf-8")
    mac = (ROOT / "run_macos.sh").read_text(encoding="utf-8")
    windows = (ROOT / "run_windows.bat").read_text(encoding="utf-8")

    assert 'STORAGE_LIFE_EXECUTION_MODE="${STORAGE_LIFE_EXECUTION_MODE:-runtime}"' in local
    assert "scripts/effective_runtime_config.py" in local
    assert 'exec "$PYTHON_BIN"' not in local

    assert "export STORAGE_LIFE_EXECUTION_MODE=runtime" in mac
    assert 'exec "$PY"' not in mac

    assert 'set "STORAGE_LIFE_EXECUTION_MODE=runtime"' in windows
    assert "effective_runtime_config.py" in (ROOT / "scripts" / "windows_start.py").read_text(encoding="utf-8")
