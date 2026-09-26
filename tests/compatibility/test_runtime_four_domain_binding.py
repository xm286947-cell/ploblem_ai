from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from runtime.binding import (
    EXPECTED_RUNTIME_DOMAINS,
    RuntimeBindingError,
    load_runtime_binding,
    validate_runtime_binding,
)


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "contracts/runtime_binding/v1/runtime_binding.json"


def test_four_domain_manifest_is_bound_to_current_tree():
    binding = load_runtime_binding(MANIFEST, repository_root=ROOT)

    assert tuple(item["domain_id"] for item in binding["domains"]) == EXPECTED_RUNTIME_DOMAINS
    assert all(item["status"] == "BOUND" for item in binding["domains"])
    assert all(item["ownership"] == "RUNTIME_EXECUTION_ONLY" for item in binding["domains"])
    assert binding["boundary"]["direct_provider_calls"] is False
    assert binding["boundary"]["domain_retry_loops"] is False
    assert binding["boundary"]["secret_persistence"] is False


def test_every_bound_agent_id_matches_its_canonical_yaml():
    binding = load_runtime_binding(MANIFEST, repository_root=ROOT)
    for domain in binding["domains"]:
        for agent_id, config_path in zip(domain["agent_ids"], domain["config_paths"]):
            raw = (ROOT / config_path).read_text(encoding="utf-8")
            assert f"agent_id: {agent_id}" in raw


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("binding_contract_version", "runtime-four-domain-binding/v0", "RUNTIME_BINDING_VERSION_MISMATCH"),
        ("runtime_contract_version", "runtime/p0.2", "RUNTIME_CONTRACT_VERSION_MISMATCH"),
        ("agent_config_contract", "other", "AGENT_CONFIG_CONTRACT_MISMATCH"),
    ],
)
def test_manifest_version_drift_fails_closed(field: str, value: str, code: str):
    binding = json.loads(MANIFEST.read_text(encoding="utf-8"))
    binding[field] = value

    with pytest.raises(RuntimeBindingError) as caught:
        validate_runtime_binding(binding, repository_root=ROOT)

    assert caught.value.code == code


def test_unknown_or_missing_domain_fails_closed():
    binding = json.loads(MANIFEST.read_text(encoding="utf-8"))
    binding["domains"] = copy.deepcopy(binding["domains"][:-1])

    with pytest.raises(RuntimeBindingError) as caught:
        validate_runtime_binding(binding, repository_root=ROOT)

    assert caught.value.code == "RUNTIME_DOMAIN_SET_MISMATCH"


def test_duplicate_agent_ids_fail_closed():
    binding = json.loads(MANIFEST.read_text(encoding="utf-8"))
    binding["domains"][1]["agent_ids"] = [binding["domains"][0]["agent_ids"][0]]

    with pytest.raises(RuntimeBindingError) as caught:
        validate_runtime_binding(binding, repository_root=ROOT)

    assert caught.value.code == "RUNTIME_AGENT_ID_DUPLICATE"


def test_domain_owned_provider_or_retry_is_rejected():
    binding = json.loads(MANIFEST.read_text(encoding="utf-8"))
    binding["boundary"]["domain_retry_loops"] = True

    with pytest.raises(RuntimeBindingError) as caught:
        validate_runtime_binding(binding, repository_root=ROOT)

    assert caught.value.code == "RUNTIME_BINDING_OWNERSHIP_VIOLATION"


def test_missing_agent_config_fails_closed(tmp_path: Path):
    binding = json.loads(MANIFEST.read_text(encoding="utf-8"))

    with pytest.raises(RuntimeBindingError) as caught:
        validate_runtime_binding(binding, repository_root=tmp_path)

    assert caught.value.code == "RUNTIME_AGENT_CONFIG_MISSING"
