"""Unified Runtime structurer for the Hardware Case internal test package.

This module is the product-side bridge only:
- model/provider/retry/secret handling stays inside Unified Runtime;
- Hardware Case owns its prompt/schema and Candidate semantics;
- real secrets are read from environment references and are never logged here.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from runtime import AgentRequest, RuntimeStatus, SqliteTaskStore
from runtime.config import AgentConfigLoader, ConfiguredAgentRuntime


AGENT_ID = "hardware_case.structure"
DEFAULT_AGENT_CONFIG = "config/runtime/agents/hardware_case.structure.yaml"
DEFAULT_LOCAL_MODEL_CONFIG = "config/runtime/model.local.yaml"

FACT_FIELDS = (
    "background",
    "symptom",
    "impact",
    "occurrence_condition",
    "analysis_process",
    "failure_mode",
    "root_cause",
    "failure_mechanism",
    "actions",
    "verification_result",
    "conclusion",
)

_FACT_SCHEMA = {
    "type": "object",
    "required": ["value", "evidence_block_ids"],
    "properties": {
        "value": {},
        "evidence_block_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
    },
    "additionalProperties": False,
}

_LINK_SCHEMA = {
    "type": "object",
    "required": ["node_id", "evidence_block_ids"],
    "properties": {
        "node_id": {"type": "string", "minLength": 1},
        "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "evidence_block_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
    },
    "additionalProperties": False,
}

HARDWARE_CASE_STRUCTURE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "product_context",
        "facts",
        "circuit_feature_links",
        "material_links",
    ],
    "properties": {
        "title": {"type": ["string", "null"]},
        "product_context": {"type": "object"},
        "facts": {
            "type": "object",
            "properties": {
                field: _FACT_SCHEMA for field in FACT_FIELDS
            },
            "additionalProperties": False,
        },
        "circuit_feature_links": {
            "type": "array",
            "items": _LINK_SCHEMA,
        },
        "material_links": {
            "type": "array",
            "items": _LINK_SCHEMA,
        },
    },
    "additionalProperties": False,
}


class HardwareCaseRuntimeConfigError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class HardwareCaseRuntimeExecutionError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolved_path(root: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def resolve_runtime_paths(
    *,
    root: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Path]:
    root_path = Path(root).resolve() if root is not None else package_root()
    env = os.environ if environ is None else environ

    model_raw = str(env.get("HARDWARE_CASE_MODEL_CONFIG") or "").strip()
    model_path = (
        _resolved_path(root_path, model_raw)
        if model_raw
        else root_path / DEFAULT_LOCAL_MODEL_CONFIG
    )
    if not model_path.is_file():
        raise HardwareCaseRuntimeConfigError("MODEL_LOCAL_CONFIG_REQUIRED")

    agent_raw = str(env.get("HARDWARE_CASE_AGENT_CONFIG") or "").strip()
    agent_path = (
        _resolved_path(root_path, agent_raw)
        if agent_raw
        else root_path / DEFAULT_AGENT_CONFIG
    )
    if not agent_path.is_file():
        raise HardwareCaseRuntimeConfigError("AGENT_CONFIG_REQUIRED")

    runtime_raw = str(env.get("HARDWARE_CASE_RUNTIME_DB") or "").strip()
    runtime_db = (
        _resolved_path(root_path, runtime_raw)
        if runtime_raw
        else root_path / "data/runtime/hardware_case_runtime.db"
    )
    return {
        "root": root_path,
        "model_config": model_path.resolve(),
        "agent_config": agent_path.resolve(),
        "runtime_db": runtime_db.resolve(),
    }


class HardwareCaseRuntimeStructurer:
    """Callable used by HardwareCaseAIAdapter / real-validation harness."""

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
    ):
        self.environ = os.environ if environ is None else environ
        self.paths = resolve_runtime_paths(root=root, environ=self.environ)
        self.paths["runtime_db"].parent.mkdir(parents=True, exist_ok=True)

        self.loader = AgentConfigLoader(
            root=self.paths["root"],
            model_profiles=self.paths["model_config"],
            schemas={"HardwareCaseStructureOutput": HARDWARE_CASE_STRUCTURE_SCHEMA},
            environ=self.environ,
        )
        self.store = SqliteTaskStore(self.paths["runtime_db"])
        self.runtime = ConfiguredAgentRuntime(
            self.store,
            config_loader=self.loader,
        )
        self.resolved = self.runtime.load_agent(self.paths["agent_config"])
        if self.resolved.definition.agent_id != AGENT_ID:
            raise HardwareCaseRuntimeConfigError("AGENT_ID_MISMATCH")

    @staticmethod
    def _request_id(document: dict[str, Any]) -> str:
        stable = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return "hardware-case-structure:" + hashlib.sha256(stable).hexdigest()[:24]

    def __call__(self, document: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(document, dict):
            raise HardwareCaseRuntimeExecutionError("DOCUMENT_OBJECT_REQUIRED")

        result = self.runtime.invoke(
            AgentRequest(
                request_id=self._request_id(document),
                agent_id=AGENT_ID,
                input=document,
                metadata={"business_domain": "HARDWARE_CASE"},
            )
        )
        if result.status != RuntimeStatus.COMPLETED:
            error = getattr(result, "error", None)
            code = str(getattr(error, "code", None) or "RUNTIME_EXECUTION_FAILED")
            raise HardwareCaseRuntimeExecutionError(code)
        if not isinstance(result.data, dict):
            raise HardwareCaseRuntimeExecutionError("RUNTIME_OUTPUT_INVALID")
        return result.data


def build_hardware_case_structurer() -> HardwareCaseRuntimeStructurer:
    """Factory resolved by tools/hardware_case_real_validation.py."""
    return HardwareCaseRuntimeStructurer()


build_hardware_case_structurer.__hardware_case_structurer_factory__ = True


__all__ = [
    "AGENT_ID",
    "HARDWARE_CASE_STRUCTURE_SCHEMA",
    "HardwareCaseRuntimeConfigError",
    "HardwareCaseRuntimeExecutionError",
    "HardwareCaseRuntimeStructurer",
    "build_hardware_case_structurer",
    "resolve_runtime_paths",
]
