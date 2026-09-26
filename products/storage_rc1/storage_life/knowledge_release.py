from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from contracts.common_evidence import COMMON_EVIDENCE_CONTRACT_VERSION


STORAGE_PRODUCT_VERSION = "STORAGE_PRODUCT_MVP_RC1"
STORAGE_CONSUMER_CONTRACT_VERSION = "UKCI-01/V1.0"
PINNED_KNOWLEDGE_RELEASE_VERSION = "KP-STORAGE-RC1-VALIDATION-001"
KNOWLEDGE_OBJECT_CONTRACT_VERSION = "knowledge-object/v1"
KNOWLEDGE_QUERY_CONTRACT_VERSION = "knowledge-query/v1"


class KnowledgeReleaseError(RuntimeError):
    pass


def _root() -> Path:
    configured = os.environ.get("STORAGE_KNOWLEDGE_RELEASE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(__file__).resolve().parents[1] / "knowledge_release" / "current").resolve()


def _binding_path(root: Path) -> Path:
    configured = os.environ.get("STORAGE_KNOWLEDGE_RELEASE_BINDING", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    local = root / "release_binding.yaml"
    if local.is_file():
        return local
    return (Path(__file__).resolve().parents[1] / "config" / "knowledge_release_binding.yaml").resolve()


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise KnowledgeReleaseError("KNOWLEDGE_RELEASE_NOT_FOUND") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise KnowledgeReleaseError("KNOWLEDGE_RELEASE_INVALID") from exc


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _terms(text: str) -> list[str]:
    return [x for x in re.split(r"[^a-zA-Z0-9_./+\-\u4e00-\u9fff]+", text.lower()) if len(x) >= 2]


@dataclass
class KnowledgeReleaseConsumer:
    root: Path

    @classmethod
    def current(cls) -> "KnowledgeReleaseConsumer":
        return cls(_root())

    def status(self) -> dict[str, Any]:
        manifest_path = self.root / "release_manifest.json"
        if not manifest_path.is_file():
            return {
                "available": False,
                "status": "PENDING",
                "code": "KNOWLEDGE_RELEASE_NOT_FOUND",
                "release_dir": str(self.root),
            }
        try:
            binding = self._validated_binding()
            manifest = self._validated_manifest()
            self._validate_binding_against_manifest(binding, manifest)
        except KnowledgeReleaseError as exc:
            return {"available": False, "status": "INVALID", "code": str(exc), "release_dir": str(self.root)}
        return {
            "available": True,
            "status": "READY",
            "knowledge_release_version": manifest.get("knowledge_release_version", ""),
            "contract_version": manifest.get("contract_version", "knowledge-query/v1"),
            "object_count": manifest.get("object_count", 0),
            "evidence_count": manifest.get("evidence_count", 0),
            "source_reference_count": manifest.get("source_reference_count", 0),
            "snapshot_hash": manifest.get("snapshot_hash", ""),
            "storage_product_version": binding["storage_product_version"],
            "storage_consumer_contract_version": binding["storage_consumer_contract_version"],
            "common_evidence_contract_version": binding["common_evidence_contract_version"],
            "release_class": binding["release_class"],
            "qualification_state": binding["qualification_state"],
        }

    def _validated_binding(self) -> dict[str, Any]:
        path = _binding_path(self.root)
        if not path.is_file():
            raise KnowledgeReleaseError("KNOWLEDGE_RELEASE_BINDING_NOT_FOUND")
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise KnowledgeReleaseError("KNOWLEDGE_RELEASE_BINDING_INVALID") from exc
        required = {
            "storage_product_version",
            "knowledge_release_version",
            "knowledge_object_contract_version",
            "knowledge_query_contract_version",
            "common_evidence_contract_version",
            "storage_consumer_contract_version",
            "compatibility_status",
            "release_class",
            "qualification_state",
            "allow_latest",
        }
        if not isinstance(payload, dict) or not required.issubset(payload):
            raise KnowledgeReleaseError("KNOWLEDGE_RELEASE_BINDING_INVALID")
        if payload.get("allow_latest") is not False:
            raise KnowledgeReleaseError("LATEST_FLOATING_DEPENDENCY")
        if str(payload.get("knowledge_release_version", "")).lower() == "latest":
            raise KnowledgeReleaseError("LATEST_FLOATING_DEPENDENCY")
        if payload.get("common_evidence_contract_version") != COMMON_EVIDENCE_CONTRACT_VERSION:
            raise KnowledgeReleaseError("COMMON_EVIDENCE_CONTRACT_VERSION_MISMATCH")
        return payload

    @staticmethod
    def _validate_binding_against_manifest(binding: dict[str, Any], manifest: dict[str, Any]) -> None:
        if binding["knowledge_release_version"] != manifest.get("knowledge_release_version"):
            raise KnowledgeReleaseError("KNOWLEDGE_RELEASE_VERSION_MISMATCH")
        if binding["knowledge_object_contract_version"] != manifest.get("object_contract_version"):
            raise KnowledgeReleaseError("KNOWLEDGE_OBJECT_CONTRACT_VERSION_MISMATCH")
        if binding["knowledge_query_contract_version"] != manifest.get("contract_version"):
            raise KnowledgeReleaseError("KNOWLEDGE_QUERY_CONTRACT_VERSION_MISMATCH")
        if binding["common_evidence_contract_version"] != manifest.get("common_evidence_contract_version"):
            raise KnowledgeReleaseError("COMMON_EVIDENCE_CONTRACT_VERSION_MISMATCH")
        if (
            binding["knowledge_release_version"] != PINNED_KNOWLEDGE_RELEASE_VERSION
            and binding.get("qualification_state") != "TEST_FIXTURE"
        ):
            raise KnowledgeReleaseError("KNOWLEDGE_RELEASE_VERSION_NOT_QUALIFIED")
        if binding["storage_consumer_contract_version"] != STORAGE_CONSUMER_CONTRACT_VERSION:
            raise KnowledgeReleaseError("STORAGE_CONSUMER_CONTRACT_VERSION_MISMATCH")

    def _validated_manifest(self) -> dict[str, Any]:
        manifest = _json(self.root / "release_manifest.json")
        if not isinstance(manifest, dict) or not str(manifest.get("knowledge_release_version") or "").strip():
            raise KnowledgeReleaseError("KNOWLEDGE_RELEASE_MANIFEST_INVALID")
        required = ("knowledge_objects.json", "evidences.json", "source_references.json")
        for name in required:
            if not (self.root / name).is_file():
                raise KnowledgeReleaseError("KNOWLEDGE_RELEASE_INCOMPLETE")
        entries = {str(x.get("path")): x for x in manifest.get("files") or [] if isinstance(x, dict)}
        for name in required:
            entry = entries.get(name)
            if entry and entry.get("sha256") and _sha256(self.root / name) != entry["sha256"]:
                raise KnowledgeReleaseError("KNOWLEDGE_RELEASE_HASH_MISMATCH")
        return manifest

    def _payload(self) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        binding = self._validated_binding()
        manifest = self._validated_manifest()
        self._validate_binding_against_manifest(binding, manifest)
        objects_raw = _json(self.root / "knowledge_objects.json")
        evidence_raw = _json(self.root / "evidences.json")
        source_raw = _json(self.root / "source_references.json")
        objects = objects_raw.get("objects", []) if isinstance(objects_raw, dict) else []
        evidences = evidence_raw.get("evidences", []) if isinstance(evidence_raw, dict) else []
        sources = source_raw.get("source_references", []) if isinstance(source_raw, dict) else []
        if not all(isinstance(x, dict) for x in objects + evidences + sources):
            raise KnowledgeReleaseError("KNOWLEDGE_RELEASE_CONTRACT_INVALID")
        ev_by_id = {str(x.get("evidence_id") or ""): x for x in evidences if x.get("evidence_id")}
        src_by_ref = {str(x.get("source_ref") or ""): x for x in sources if x.get("source_ref")}
        return manifest, objects, ev_by_id, src_by_ref

    def query(self, text: str, *, device_type: str = "", top_k: int = 8) -> dict[str, Any]:
        manifest, objects, ev_by_id, src_by_ref = self._payload()
        terms = _terms(text)
        ranked = []
        for obj in objects:
            if str(obj.get("status") or "ACTIVE") != "ACTIVE":
                continue
            if device_type and str(obj.get("device_type") or "").lower() not in {device_type.lower(), "generic", ""}:
                continue
            hay = " ".join(str(obj.get(k) or "") for k in ("title", "summary", "content", "device_type"))
            hay += " " + " ".join(map(str, obj.get("tags") or [])) + " " + " ".join(map(str, obj.get("scope") or []))
            lower = hay.lower()
            score = sum(3 if t in str(obj.get("title") or "").lower() else 1 for t in terms if t in lower)
            if terms and score == 0:
                continue
            evidence = [ev_by_id[eid] for eid in obj.get("evidence_refs") or [] if eid in ev_by_id]
            sources = [src_by_ref[sid] for sid in obj.get("source_refs") or [] if sid in src_by_ref]
            ranked.append((score, str(obj.get("object_id") or ""), {**obj, "evidence": evidence, "source_references": sources}))
        ranked.sort(key=lambda x: (-x[0], x[1]))
        items = [x[2] for x in ranked[: max(1, min(int(top_k), 50))]]
        return {
            "contract_version": manifest.get("contract_version", "knowledge-query/v1"),
            "knowledge_release_version": manifest.get("knowledge_release_version"),
            "results": items,
            "total": len(items),
            "unknowns_or_gaps": [] if items else ["NO_MATCHING_PUBLISHED_KNOWLEDGE"],
        }

    def evidence(self, evidence_id: str) -> dict[str, Any]:
        manifest, _, ev_by_id, _ = self._payload()
        if evidence_id not in ev_by_id:
            raise KnowledgeReleaseError("EVIDENCE_NOT_FOUND")
        return {
            "knowledge_release_version": manifest.get("knowledge_release_version"),
            "evidence": ev_by_id[evidence_id],
        }
