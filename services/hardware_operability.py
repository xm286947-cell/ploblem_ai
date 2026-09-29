"""Release binding, compatibility and operability checks for Hardware Case."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from runtime.config import AgentConfigLoader
from services.hardware_case_runtime_adapter import (
    HARDWARE_CASE_STRUCTURE_SCHEMA,
    resolve_runtime_paths,
)
from services.hardware_data_reliability import (
    HardwareDataReliabilityManager,
    SCHEMA_VERSION_NAME,
)
from services.hardware_public_consumer import PUBLIC_CONTRACT_VERSION


PRODUCT_VERSION = "MVP_V0.1"
PUBLIC_API_VERSION = "v1"
SCHEMA_BASELINE = SCHEMA_VERSION_NAME
COMPATIBILITY_MATRIX = {
    "v1": {
        "contract_version": PUBLIC_CONTRACT_VERSION,
        "status": "ACTIVE",
        "deprecated": False,
        "breaking_change_policy": "NEW_MAJOR_VERSION_REQUIRED",
        "additive_change_policy": "ALLOWED_IF_BACKWARD_COMPATIBLE",
    }
}


def release_binding(root: str | Path) -> dict[str, Any]:
    root_path = Path(root)
    manifest_path = root_path / "PACKAGE_MANIFEST.json"
    source_commit = str(
        os.environ.get("HARDWARE_RELEASE_SOURCE_COMMIT") or ""
    ).strip()
    product_version = PRODUCT_VERSION
    schema_version = SCHEMA_BASELINE
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
        source_commit = str(manifest.get("source_commit") or source_commit or "UNKNOWN")
        product_version = str(manifest.get("product_version") or manifest.get("target_version") or PRODUCT_VERSION)
        schema_version = str(manifest.get("schema_version") or SCHEMA_BASELINE)
    return {
        "source_commit": source_commit or "SOURCE_TREE",
        "product_version": product_version,
        "schema_version": schema_version,
        "public_contract_version": PUBLIC_CONTRACT_VERSION,
        "public_api_version": PUBLIC_API_VERSION,
    }


def contract_descriptor(root: str | Path) -> dict[str, Any]:
    return {
        **release_binding(root),
        "compatibility": COMPATIBILITY_MATRIX,
        "deprecated": False,
        "historical_deprecated_case_access": "EXPLICIT_HISTORICAL_TRUE_ONLY",
    }


def health_status() -> dict[str, Any]:
    return {"status": "HEALTHY", "service": "HARDWARE_CASE"}


def _db_ready(db_path: Path) -> dict[str, Any]:
    status = HardwareDataReliabilityManager(db_path).inspect_status()
    if status.get("status") != "READY":
        return {
            "status": "UNREADY",
            "error_code": str(status.get("error_code") or "HARDWARE_DB_UNREADY"),
            "schema_version": status.get("schema_version"),
            "schema_name": status.get("schema_name"),
        }
    return {
        "status": "READY",
        "schema_version": status.get("schema_version"),
        "schema_name": status.get("schema_name"),
        "fingerprint": status.get("fingerprint"),
    }


def _contract_ready(root: Path) -> dict[str, Any]:
    path = root / "schema/hardware_public_consumer_v1.schema.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("$id") != "https://contracts.local/hardware-public-consumer/v1":
            raise ValueError("contract id mismatch")
    except Exception:
        return {"status": "UNREADY", "error_code": "PUBLIC_CONTRACT_INVALID"}
    return {"status": "READY", "contract_version": PUBLIC_CONTRACT_VERSION}


def _runtime_ready(root: Path, environ: Mapping[str, str]) -> dict[str, Any]:
    try:
        paths = resolve_runtime_paths(root=root, environ=environ)
        loader = AgentConfigLoader(
            root=paths["root"],
            model_profiles=paths["model_config"],
            schemas={"HardwareCaseStructureOutput": HARDWARE_CASE_STRUCTURE_SCHEMA},
            environ=environ,
        )
        resolved = loader.load(paths["agent_config"])
        if resolved.definition.agent_id != "hardware_case.structure":
            raise ValueError("agent id mismatch")
        return {
            "status": "READY",
            "agent_id": resolved.definition.agent_id,
            "config_hash": resolved.config_hash[:16],
        }
    except Exception as error:
        code = str(getattr(error, "code", None) or type(error).__name__)
        return {"status": "UNREADY", "error_code": "RUNTIME_CONFIG_INVALID:" + code}


def _knowledge_ready(environ: Mapping[str, str]) -> dict[str, Any]:
    base_url = str(environ.get("HARDWARE_KNOWLEDGE_BASE_URL") or "").strip()
    release_version = str(
        environ.get("HARDWARE_KNOWLEDGE_RELEASE_VERSION") or ""
    ).strip()
    readiness_url = str(
        environ.get("HARDWARE_KNOWLEDGE_READINESS_URL") or ""
    ).strip()
    if not base_url or not release_version or not readiness_url:
        return {"status": "UNREADY", "error_code": "KNOWLEDGE_CONFIG_REQUIRED"}
    if not base_url.startswith(("http://", "https://")) or not readiness_url.startswith(("http://", "https://")):
        return {"status": "UNREADY", "error_code": "KNOWLEDGE_CONFIG_INVALID"}
    try:
        request = Request(readiness_url, method="GET", headers={"Accept": "application/json"})
        with urlopen(request, timeout=2.0) as response:
            if not 200 <= int(response.status) < 300:
                return {"status": "UNREADY", "error_code": "KNOWLEDGE_UNAVAILABLE"}
    except (HTTPError, URLError, TimeoutError, OSError):
        return {"status": "UNREADY", "error_code": "KNOWLEDGE_UNAVAILABLE"}
    return {"status": "READY", "release_version": release_version}


def readiness_status(
    *,
    root: str | Path,
    hardware_db_path: str | Path,
    environ: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    root_path = Path(root).resolve()
    env = os.environ if environ is None else environ
    dependencies = {
        "HARDWARE_DB": _db_ready(Path(hardware_db_path)),
        "PUBLIC_CONTRACT": _contract_ready(root_path),
        "UNIFIED_RUNTIME_CONFIG": _runtime_ready(root_path, env),
        "UNIFIED_KNOWLEDGE": _knowledge_ready(env),
    }
    ready = all(item.get("status") == "READY" for item in dependencies.values())
    payload = {
        "status": "READY" if ready else "UNREADY",
        "service": "HARDWARE_CASE",
        "dependencies": dependencies,
        "binding": release_binding(root_path),
    }
    return (200 if ready else 503), payload


__all__ = [
    "COMPATIBILITY_MATRIX",
    "PRODUCT_VERSION",
    "PUBLIC_API_VERSION",
    "SCHEMA_BASELINE",
    "contract_descriptor",
    "health_status",
    "readiness_status",
    "release_binding",
]
