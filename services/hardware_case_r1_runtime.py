"""Unified Runtime adapter for Hardware R1 extraction/v2.

This is a dedicated preview Agent on the existing Unified Runtime. It shares
the same model configuration and Runtime DB as Hardware Case, but it has no
Case/Mapping/Knowledge persistence side effects.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from runtime import AgentRequest, RuntimeStatus, SqliteTaskStore
from runtime.config import AgentConfigLoader, ConfiguredAgentRuntime
from services.hardware_case_runtime_adapter import (
    DEFAULT_LOCAL_MODEL_CONFIG,
    HardwareCaseRuntimeConfigError,
    HardwareCaseRuntimeExecutionError,
    package_root,
)


R1_AGENT_ID = "hardware_case.r1_extract"
R1_EXTRACTION_CONTRACT_VERSION = "hardware-r1-extraction/v2"
DEFAULT_R1_AGENT_CONFIG = "config/runtime/agents/hardware_case.r1_extract.yaml"
DEFAULT_RUNTIME_DB = "data/runtime/hardware_case_runtime.db"

EXTRACTION_STATUSES = ["EXTRACTED", "MISSING", "AMBIGUOUS", "UNSUPPORTED"]
REVIEW_STATUSES = ["UNREVIEWED", "CONFIRMED", "REJECTED", "EDITED"]
CONFLICT_STATUSES = ["OPEN", "CONFIRMED", "RESOLVED", "ACCEPTED_AS_IS"]

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

_CONTEXT_FIELDS = (
    "primary_subject",
    "component_or_device",
    "interface",
    "signal",
    "peer_device_or_load",
)

_CANDIDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["value", "extraction_status", "evidence_block_ids", "warnings"],
    "properties": {
        "value": {},
        "extraction_status": {"type": "string", "enum": EXTRACTION_STATUSES},
        "evidence_block_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
        "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "additionalProperties": False,
}

_KEY_PARAMETER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "name",
        "value",
        "extraction_status",
        "evidence_block_ids",
        "warnings",
    ],
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "value": {},
        "unit": {"type": ["string", "null"]},
        "extraction_status": {"type": "string", "enum": EXTRACTION_STATUSES},
        "evidence_block_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
        "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "additionalProperties": False,
}

_REUSABLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "value",
        "extraction_status",
        "evidence_block_ids",
        "warnings",
        "derived_from_fields",
        "review_status",
    ],
    "properties": {
        **_CANDIDATE_SCHEMA["properties"],
        "derived_from_fields": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
        "review_status": {"type": "string", "enum": REVIEW_STATUSES},
    },
    "additionalProperties": False,
}

_CONFLICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "type",
        "field",
        "source_values",
        "evidence_block_ids",
        "status",
    ],
    "properties": {
        "conflict_id": {"type": ["string", "null"]},
        "type": {
            "type": "string",
            "enum": [
                "TITLE_CONTENT_SUBJECT_MISMATCH",
                "SOURCE_VALUE_CONFLICT",
                "AI_SOURCE_CONFLICT",
                "MULTI_SOURCE_CONFLICT",
            ],
        },
        "field": {"type": "string", "minLength": 1},
        "source_values": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["source", "value"],
                "properties": {
                    "source": {"type": "string", "minLength": 1},
                    "value": {},
                },
                "additionalProperties": False,
            },
        },
        "evidence_block_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
        "status": {"type": "string", "enum": CONFLICT_STATUSES},
        "resolution_status": {
            "type": ["string", "null"],
            "enum": ["NEEDS_REVIEW", "CONFIRMED", "RESOLVED", "ACCEPTED_AS_IS", None],
        },
        "reviewer_note": {"type": ["string", "null"]},
    },
    "additionalProperties": False,
}

HARDWARE_R1_EXTRACTION_V2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "contract_version",
        "engineering_context",
        "facts",
        "conflicts",
        "reusable_knowledge_candidate",
    ],
    "properties": {
        "contract_version": {
            "type": "string",
            "enum": [R1_EXTRACTION_CONTRACT_VERSION],
        },
        "engineering_context": {
            "type": "object",
            "required": [*_CONTEXT_FIELDS, "key_parameters"],
            "properties": {
                **{name: _CANDIDATE_SCHEMA for name in _CONTEXT_FIELDS},
                "key_parameters": {
                    "type": "array",
                    "items": _KEY_PARAMETER_SCHEMA,
                },
            },
            "additionalProperties": False,
        },
        "facts": {
            "type": "object",
            "required": list(FACT_FIELDS),
            "properties": {name: _CANDIDATE_SCHEMA for name in FACT_FIELDS},
            "additionalProperties": False,
        },
        "conflicts": {
            "type": "array",
            "items": _CONFLICT_SCHEMA,
        },
        "reusable_knowledge_candidate": {
            "type": "object",
            "required": [
                "engineering_rule",
                "design_constraint",
                "diagnostic_clue",
                "verification_method",
                "applicability",
                "conclusion",
            ],
            "properties": {
                name: _REUSABLE_SCHEMA
                for name in (
                    "engineering_rule",
                    "design_constraint",
                    "diagnostic_clue",
                    "verification_method",
                    "applicability",
                    "conclusion",
                )
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


def _resolved_path(root: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def resolve_r1_runtime_paths(
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

    agent_raw = str(env.get("HARDWARE_CASE_R1_AGENT_CONFIG") or "").strip()
    agent_path = (
        _resolved_path(root_path, agent_raw)
        if agent_raw
        else root_path / DEFAULT_R1_AGENT_CONFIG
    )
    if not agent_path.is_file():
        raise HardwareCaseRuntimeConfigError("R1_AGENT_CONFIG_REQUIRED")

    runtime_raw = str(env.get("HARDWARE_CASE_RUNTIME_DB") or "").strip()
    runtime_db = (
        _resolved_path(root_path, runtime_raw)
        if runtime_raw
        else root_path / DEFAULT_RUNTIME_DB
    )
    return {
        "root": root_path,
        "model_config": model_path.resolve(),
        "agent_config": agent_path.resolve(),
        "runtime_db": runtime_db.resolve(),
    }


class HardwareCaseR1RuntimeStructurer:
    """Callable R1 v2 extractor using the shared Unified Runtime."""

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
    ):
        self.environ = os.environ if environ is None else environ
        self.paths = resolve_r1_runtime_paths(root=root, environ=self.environ)
        self.paths["runtime_db"].parent.mkdir(parents=True, exist_ok=True)
        self.loader = AgentConfigLoader(
            root=self.paths["root"],
            model_profiles=self.paths["model_config"],
            schemas={"HardwareCaseR1ExtractionV2": HARDWARE_R1_EXTRACTION_V2_SCHEMA},
            environ=self.environ,
        )
        self.store = SqliteTaskStore(self.paths["runtime_db"])
        self.runtime = ConfiguredAgentRuntime(self.store, config_loader=self.loader)
        self.resolved = self.runtime.load_agent(self.paths["agent_config"])
        if self.resolved.definition.agent_id != R1_AGENT_ID:
            raise HardwareCaseRuntimeConfigError("R1_AGENT_ID_MISMATCH")

    @staticmethod
    def _request_id(document: dict[str, Any]) -> str:
        stable = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return "hardware-case-r1-v2:" + hashlib.sha256(stable).hexdigest()[:24]

    def __call__(self, document: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(document, dict):
            raise HardwareCaseRuntimeExecutionError("DOCUMENT_OBJECT_REQUIRED")
        result = self.runtime.invoke(
            AgentRequest(
                request_id=self._request_id(document),
                agent_id=R1_AGENT_ID,
                input=document,
                metadata={"business_domain": "HARDWARE_CASE", "preview_only": True},
            )
        )
        if result.status != RuntimeStatus.COMPLETED:
            error = getattr(result, "error", None)
            code = str(getattr(error, "code", None) or "RUNTIME_EXECUTION_FAILED")
            raise HardwareCaseRuntimeExecutionError(code)
        if not isinstance(result.data, dict):
            raise HardwareCaseRuntimeExecutionError("RUNTIME_OUTPUT_INVALID")
        return {
            **result.data,
            "__runtime_meta__": {
                "run_id": result.run_id,
                "task_id": result.task_id,
                "agent_id": result.agent_id,
                "agent_config_version": self.resolved.definition.version,
                "agent_config_hash": self.resolved.config_hash,
            },
        }


def build_hardware_case_r1_structurer() -> HardwareCaseR1RuntimeStructurer:
    return HardwareCaseR1RuntimeStructurer()


build_hardware_case_r1_structurer.__hardware_case_r1_structurer_factory__ = True


__all__ = [
    "R1_AGENT_ID",
    "R1_EXTRACTION_CONTRACT_VERSION",
    "HARDWARE_R1_EXTRACTION_V2_SCHEMA",
    "HardwareCaseR1RuntimeStructurer",
    "build_hardware_case_r1_structurer",
    "resolve_r1_runtime_paths",
]
