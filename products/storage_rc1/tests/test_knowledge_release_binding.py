from __future__ import annotations

from pathlib import Path

import yaml

from storage_life.knowledge_release import KnowledgeReleaseConsumer


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "knowledge_release" / "current"


def test_storage_rc1_uses_explicit_pinned_release_binding(monkeypatch):
    monkeypatch.setenv("STORAGE_KNOWLEDGE_RELEASE_DIR", str(RELEASE))
    monkeypatch.delenv("STORAGE_KNOWLEDGE_RELEASE_BINDING", raising=False)
    status = KnowledgeReleaseConsumer.current().status()
    assert status["available"] is True
    assert status["knowledge_release_version"] == "KP-STORAGE-RC1-VALIDATION-001"
    assert status["common_evidence_contract_version"] == "common-evidence/v1.0"
    assert status["release_class"] == "CONTROLLED_CONSUMER_VALIDATION"
    assert status["qualification_state"] == "VALIDATION_ONLY"


def test_storage_rc1_fails_closed_for_latest_binding(tmp_path, monkeypatch):
    binding = tmp_path / "binding.yaml"
    payload = yaml.safe_load((ROOT / "config" / "knowledge_release_binding.yaml").read_text(encoding="utf-8"))
    payload["knowledge_release_version"] = "latest"
    binding.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("STORAGE_KNOWLEDGE_RELEASE_DIR", str(RELEASE))
    monkeypatch.setenv("STORAGE_KNOWLEDGE_RELEASE_BINDING", str(binding))
    status = KnowledgeReleaseConsumer.current().status()
    assert status["available"] is False
    assert status["code"] == "LATEST_FLOATING_DEPENDENCY"


def test_storage_rc1_fails_closed_for_contract_mismatch(tmp_path, monkeypatch):
    binding = tmp_path / "binding.yaml"
    payload = yaml.safe_load((ROOT / "config" / "knowledge_release_binding.yaml").read_text(encoding="utf-8"))
    payload["common_evidence_contract_version"] = "common-evidence/v2.0"
    binding.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("STORAGE_KNOWLEDGE_RELEASE_DIR", str(RELEASE))
    monkeypatch.setenv("STORAGE_KNOWLEDGE_RELEASE_BINDING", str(binding))
    status = KnowledgeReleaseConsumer.current().status()
    assert status["available"] is False
    assert status["code"] == "COMMON_EVIDENCE_CONTRACT_VERSION_MISMATCH"


def test_storage_rc1_fails_closed_when_binding_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_KNOWLEDGE_RELEASE_DIR", str(RELEASE))
    monkeypatch.setenv("STORAGE_KNOWLEDGE_RELEASE_BINDING", str(tmp_path / "missing.yaml"))
    status = KnowledgeReleaseConsumer.current().status()
    assert status["available"] is False
    assert status["code"] == "KNOWLEDGE_RELEASE_BINDING_NOT_FOUND"
