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


# === V1.3 PIPELINE RUNTIME ===
# V1.2 is intentionally retained above as a compatibility seam for frozen
# regression tests. The production factory below is re-bound to the V1.3
# two-stage pipeline.
import sqlite3 as _sqlite3
from datetime import datetime as _datetime, timezone as _timezone
from uuid import uuid4 as _uuid4

from runtime.reliability import ExistingTaskNotCompleteError as _ExistingTaskNotCompleteError


R1_STAGE_A_AGENT_ID = "hardware_case.r1_case_extract"
R1_STAGE_B_AGENT_ID = "hardware_case.r1_reuse_derive"
R1_STAGE_A_CONFIG = "config/runtime/agents/hardware_case.r1_case_extract.yaml"
R1_STAGE_B_CONFIG = "config/runtime/agents/hardware_case.r1_reuse_derive.yaml"
R1_STAGE_A_SCHEMA_REF = "HardwareCaseR1CaseExtractionV13"
R1_STAGE_B_SCHEMA_REF = "HardwareCaseR1ReusableKnowledgeV13"
R1_PIPELINE_VERSION = "hardware-r1-agent-pipeline/v1.3.1"

_V13_STATUS_ENUM = ["EXTRACTED", "MISSING", "AMBIGUOUS", "UNSUPPORTED"]
_V13_MODEL_FIELD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["value", "status", "evidence_block_ids"],
    "properties": {
        "value": {},
        "status": {"type": "string", "enum": _V13_STATUS_ENUM},
        "evidence_block_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
    },
    "additionalProperties": False,
}
_V13_KEY_PARAMETER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name", "value", "status", "evidence_block_ids"],
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "value": {},
        "unit": {"type": ["string", "null"]},
        "status": {"type": "string", "enum": _V13_STATUS_ENUM},
        "evidence_block_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
    },
    "additionalProperties": False,
}
HARDWARE_R1_STAGE_A_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["engineering_context", "facts"],
    "properties": {
        "engineering_context": {
            "type": "object",
            "required": [*_CONTEXT_FIELDS, "key_parameters"],
            "properties": {
                **{name: _V13_MODEL_FIELD_SCHEMA for name in _CONTEXT_FIELDS},
                "key_parameters": {
                    "type": "array",
                    "items": _V13_KEY_PARAMETER_SCHEMA,
                },
            },
            "additionalProperties": False,
        },
        "facts": {
            "type": "object",
            "required": list(FACT_FIELDS),
            "properties": {
                name: _V13_MODEL_FIELD_SCHEMA for name in FACT_FIELDS
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}
_V13_REUSABLE_MODEL_FIELD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["value", "status", "derived_from_fields", "evidence_block_ids"],
    "properties": {
        "value": {},
        "status": {"type": "string", "enum": _V13_STATUS_ENUM},
        "derived_from_fields": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
        "evidence_block_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
    },
    "additionalProperties": False,
}
HARDWARE_R1_STAGE_B_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["reusable_knowledge_candidate"],
    "properties": {
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
                name: _V13_REUSABLE_MODEL_FIELD_SCHEMA
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
        }
    },
    "additionalProperties": False,
}


def map_r1_runtime_error(
    code: str | None,
    *,
    category: str | None = None,
    details: Mapping[str, Any] | None = None,
    retry_budget_exhausted: bool = False,
    validation_retry_count: int = 0,
) -> str:
    raw = str(code or "").strip().upper()
    category = str(category or "").strip().upper()
    details = dict(details or {})
    exception_type = str(details.get("exception_type") or "").upper()

    if raw in {
        "MODEL_LOCAL_CONFIG_REQUIRED",
        "R1_AGENT_CONFIG_REQUIRED",
        "R1_CASE_AGENT_CONFIG_REQUIRED",
        "R1_REUSE_AGENT_CONFIG_REQUIRED",
        "AGENT_CONFIG_REQUIRED",
        "AGENT_ID_MISMATCH",
        "R1_AGENT_ID_MISMATCH",
    } or "CONFIG" in raw and "PROVIDER" not in raw:
        return "RUNTIME_CONFIG_MISSING"
    if "TIMEOUT" in raw or "TIMEOUT" in exception_type:
        return "PROVIDER_TIMEOUT"
    if raw in {"PROVIDER_TRANSPORT", "PROVIDER_TRANSPORT_FAILED"}:
        return "PROVIDER_TRANSPORT_FAILED"
    if category == "VALIDATION" or raw in {
        "PROVIDER_SCHEMA_INVALID",
        "PROVIDER_OUTPUT_SHAPE_INVALID",
        "PROVIDER_ENVELOPE_INVALID",
        "INVALID_JSON",
        "OUTPUT_TRUNCATED",
        "EMPTY_PROVIDER_CONTENT",
    }:
        if validation_retry_count > 0 or retry_budget_exhausted:
            return "VALIDATION_RETRY_EXHAUSTED"
        return "OUTPUT_SCHEMA_INVALID"
    if raw in {"RETRY_BUDGET_EXHAUSTED", "PROVIDER_CALL_BUDGET_EXHAUSTED"}:
        return "PROVIDER_CALL_BUDGET_EXHAUSTED"
    if retry_budget_exhausted:
        return "PROVIDER_CALL_BUDGET_EXHAUSTED"
    return "RUNTIME_EXECUTION_FAILED"


def _v13_paths(
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

    stage_a_raw = str(env.get("HARDWARE_CASE_R1_CASE_AGENT_CONFIG") or "").strip()
    stage_b_raw = str(env.get("HARDWARE_CASE_R1_REUSE_AGENT_CONFIG") or "").strip()
    stage_a_path = (
        _resolved_path(root_path, stage_a_raw)
        if stage_a_raw
        else root_path / R1_STAGE_A_CONFIG
    )
    stage_b_path = (
        _resolved_path(root_path, stage_b_raw)
        if stage_b_raw
        else root_path / R1_STAGE_B_CONFIG
    )
    if not stage_a_path.is_file():
        raise HardwareCaseRuntimeConfigError("R1_CASE_AGENT_CONFIG_REQUIRED")
    if not stage_b_path.is_file():
        raise HardwareCaseRuntimeConfigError("R1_REUSE_AGENT_CONFIG_REQUIRED")

    runtime_raw = str(env.get("HARDWARE_CASE_RUNTIME_DB") or "").strip()
    runtime_db = (
        _resolved_path(root_path, runtime_raw)
        if runtime_raw
        else root_path / DEFAULT_RUNTIME_DB
    )
    return {
        "root": root_path,
        "model_config": model_path.resolve(),
        "stage_a_config": stage_a_path.resolve(),
        "stage_b_config": stage_b_path.resolve(),
        "runtime_db": runtime_db.resolve(),
    }


class _R1StageCache:
    """Successful-stage cache only. Failed runs are never cached."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with _sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hardware_r1_stage_cache (
                    stage TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    runtime_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(stage, cache_key)
                )
                """
            )

    def get(self, stage: str, cache_key: str) -> dict[str, Any] | None:
        with _sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT result_json, runtime_json, created_at
                FROM hardware_r1_stage_cache
                WHERE stage=? AND cache_key=?
                """,
                (stage, cache_key),
            ).fetchone()
        if row is None:
            return None
        return {
            "data": json.loads(row[0]),
            "runtime": json.loads(row[1]),
            "created_at": row[2],
        }

    def put(
        self,
        stage: str,
        cache_key: str,
        data: dict[str, Any],
        runtime_meta: dict[str, Any],
    ) -> None:
        with _sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO hardware_r1_stage_cache(
                    stage, cache_key, result_json, runtime_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(stage, cache_key) DO UPDATE SET
                    result_json=excluded.result_json,
                    runtime_json=excluded.runtime_json,
                    created_at=excluded.created_at
                """,
                (
                    stage,
                    cache_key,
                    json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(runtime_meta, ensure_ascii=False, separators=(",", ":")),
                    _datetime.now(_timezone.utc).isoformat(),
                ),
            )


def _attempt_metrics(runtime_db: Path, task_id: str) -> dict[str, Any]:
    call_ms: list[int] = []
    validation_cycles: dict[int, int] = {}
    prompt_tokens: list[int] = []
    completion_tokens: list[int] = []
    try:
        with _sqlite3.connect(runtime_db) as connection:
            rows = connection.execute(
                """
                SELECT a.record_json
                FROM runtime_attempt a
                JOIN runtime_step_run s ON s.step_run_id=a.step_run_id
                JOIN runtime_run r ON r.run_id=s.run_id
                WHERE r.task_id=?
                ORDER BY a.rowid
                """,
                (task_id,),
            ).fetchall()
    except Exception:
        rows = []

    for row in rows:
        try:
            record = json.loads(row[0])
        except Exception:
            continue
        started = record.get("started_at")
        completed = record.get("completed_at")
        if started and completed:
            try:
                start_dt = _datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                end_dt = _datetime.fromisoformat(str(completed).replace("Z", "+00:00"))
                call_ms.append(max(0, int((end_dt - start_dt).total_seconds() * 1000)))
            except Exception:
                pass
        step_attempt = int(record.get("step_attempt_no") or 1)
        cycle = int(record.get("validation_cycle_no") or 1)
        validation_cycles[step_attempt] = max(
            validation_cycles.get(step_attempt, 1),
            cycle,
        )
        metrics = record.get("execution_metrics") or {}
        if isinstance(metrics.get("prompt_tokens"), int):
            prompt_tokens.append(int(metrics["prompt_tokens"]))
        if isinstance(metrics.get("completion_tokens"), int):
            completion_tokens.append(int(metrics["completion_tokens"]))

    return {
        "provider_call_ms": call_ms,
        "validation_retry_count": sum(
            max(0, cycle - 1) for cycle in validation_cycles.values()
        ),
        "prompt_tokens": sum(prompt_tokens) if prompt_tokens else "UNKNOWN",
        "completion_tokens": (
            sum(completion_tokens) if completion_tokens else "UNKNOWN"
        ),
    }


class _R1StageRunner:
    def __init__(
        self,
        *,
        paths: dict[str, Path],
        config_key: str,
        expected_agent_id: str,
        schema_ref: str,
        cache: _R1StageCache,
        stage_name: str,
        environ: Mapping[str, str],
    ):
        self.paths = paths
        self.stage_name = stage_name
        self.expected_agent_id = expected_agent_id
        self.cache = cache
        loader = AgentConfigLoader(
            root=paths["root"],
            model_profiles=paths["model_config"],
            schemas={
                R1_STAGE_A_SCHEMA_REF: HARDWARE_R1_STAGE_A_SCHEMA,
                R1_STAGE_B_SCHEMA_REF: HARDWARE_R1_STAGE_B_SCHEMA,
            },
            environ=environ,
        )
        self.store = SqliteTaskStore(paths["runtime_db"])
        self.runtime = ConfiguredAgentRuntime(self.store, config_loader=loader)
        self.resolved = self.runtime.load_agent(paths[config_key])
        if self.resolved.definition.agent_id != expected_agent_id:
            raise HardwareCaseRuntimeConfigError("R1_AGENT_ID_MISMATCH")
        if self.resolved.output_schema.ref != schema_ref:
            raise HardwareCaseRuntimeConfigError("R1_OUTPUT_SCHEMA_MISMATCH")

    def _cache_key(self, *, source_id: str, markdown_hash: str) -> str:
        material = {
            "source_id": str(source_id),
            "markdown_hash": str(markdown_hash),
            "agent_config_hash": self.resolved.config_hash,
            "prompt_version": (
                self.resolved.prompt.version or self.resolved.prompt.content_hash
            ),
        }
        encoded = json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _runtime_meta(self, result: Any, *, cache_hit: bool = False) -> dict[str, Any]:
        attempts = _attempt_metrics(self.paths["runtime_db"], result.task_id)
        error = getattr(result, "error", None)
        execution = getattr(result, "execution", None)
        return {
            "run_id": result.run_id,
            "task_id": result.task_id,
            "agent_id": result.agent_id,
            "agent_config_version": self.resolved.definition.version,
            "agent_config_hash": self.resolved.config_hash,
            "prompt_version": (
                self.resolved.prompt.version or self.resolved.prompt.content_hash
            ),
            "provider_call_count": int(getattr(execution, "provider_calls", 0) or 0),
            "provider_call_ms": attempts["provider_call_ms"],
            "prompt_tokens": attempts["prompt_tokens"],
            "completion_tokens": attempts["completion_tokens"],
            "validation_retry_count": attempts["validation_retry_count"],
            "retry_budget_exhausted": bool(
                getattr(execution, "retry_budget_exhausted", False)
            ),
            "raw_error_code": (
                str(getattr(error, "code", "") or "") or None
            ),
            "cache_hit": cache_hit,
        }

    def run(
        self,
        payload: dict[str, Any],
        *,
        source_id: str,
        markdown_hash: str,
        force_retry: bool = False,
    ) -> dict[str, Any]:
        cache_key = self._cache_key(
            source_id=source_id,
            markdown_hash=markdown_hash,
        )
        if not force_retry:
            cached = self.cache.get(self.stage_name, cache_key)
            if cached is not None:
                runtime_meta = dict(cached["runtime"])
                runtime_meta.update(
                    {
                        "cache_hit": True,
                        "cached_from_run_id": runtime_meta.get("run_id"),
                        "provider_call_count": 0,
                        "provider_call_ms": [],
                        "validation_retry_count": 0,
                        "prompt_tokens": "UNKNOWN",
                        "completion_tokens": "UNKNOWN",
                    }
                )
                return {
                    "ok": True,
                    "data": cached["data"],
                    "runtime": runtime_meta,
                    "cache_key": cache_key,
                }

        base_request_id = (
            f"hardware-r1-v13:{self.stage_name.lower()}:{cache_key[:32]}"
        )
        request_id = (
            f"{base_request_id}:force:{_uuid4().hex[:12]}"
            if force_retry
            else base_request_id
        )

        def invoke(one_request_id: str):
            return self.runtime.invoke(
                AgentRequest(
                    request_id=one_request_id,
                    agent_id=self.expected_agent_id,
                    input=payload,
                    metadata={
                        "business_domain": "HARDWARE_CASE",
                        "preview_only": True,
                        "pipeline_stage": self.stage_name,
                        "cache_key": cache_key,
                        "force_retry": bool(force_retry),
                    },
                )
            )

        try:
            result = invoke(request_id)
        except _ExistingTaskNotCompleteError:
            # Canonical request IDs deliberately keep the failed Runtime task as
            # immutable evidence. Retry receives a fresh task/run identity.
            result = invoke(f"{base_request_id}:retry:{_uuid4().hex[:12]}")

        runtime_meta = self._runtime_meta(result)
        if result.status == RuntimeStatus.COMPLETED and isinstance(result.data, dict):
            self.cache.put(
                self.stage_name,
                cache_key,
                result.data,
                runtime_meta,
            )
            return {
                "ok": True,
                "data": result.data,
                "runtime": runtime_meta,
                "cache_key": cache_key,
            }

        error = getattr(result, "error", None)
        category = getattr(getattr(error, "category", None), "value", None)
        details = getattr(error, "details", None) or {}
        mapped = map_r1_runtime_error(
            getattr(error, "code", None),
            category=category,
            details=details,
            retry_budget_exhausted=runtime_meta["retry_budget_exhausted"],
            validation_retry_count=runtime_meta["validation_retry_count"],
        )
        return {
            "ok": False,
            "data": None,
            "error_code": mapped,
            "raw_error_code": runtime_meta.get("raw_error_code"),
            "runtime": runtime_meta,
            "cache_key": cache_key,
        }


class HardwareCaseR1PipelineRuntime:
    """V1.3 two-stage Runtime facade with independent Runs and success cache."""

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
    ):
        env = os.environ if environ is None else environ
        self.paths = _v13_paths(root=root, environ=env)
        self.paths["runtime_db"].parent.mkdir(parents=True, exist_ok=True)
        cache = _R1StageCache(self.paths["runtime_db"])
        self.stage_a = _R1StageRunner(
            paths=self.paths,
            config_key="stage_a_config",
            expected_agent_id=R1_STAGE_A_AGENT_ID,
            schema_ref=R1_STAGE_A_SCHEMA_REF,
            cache=cache,
            stage_name="STAGE_A",
            environ=env,
        )
        self.stage_b = _R1StageRunner(
            paths=self.paths,
            config_key="stage_b_config",
            expected_agent_id=R1_STAGE_B_AGENT_ID,
            schema_ref=R1_STAGE_B_SCHEMA_REF,
            cache=cache,
            stage_name="STAGE_B",
            environ=env,
        )

    def run_stage_a(
        self,
        payload: dict[str, Any],
        *,
        source_id: str,
        markdown_hash: str,
        force_retry: bool = False,
    ) -> dict[str, Any]:
        return self.stage_a.run(
            payload,
            source_id=source_id,
            markdown_hash=markdown_hash,
            force_retry=force_retry,
        )

    def run_stage_b(
        self,
        payload: dict[str, Any],
        *,
        source_id: str,
        markdown_hash: str,
        force_retry: bool = False,
    ) -> dict[str, Any]:
        return self.stage_b.run(
            payload,
            source_id=source_id,
            markdown_hash=markdown_hash,
            force_retry=force_retry,
        )


def build_hardware_case_r1_structurer() -> HardwareCaseR1PipelineRuntime:
    return HardwareCaseR1PipelineRuntime()


build_hardware_case_r1_structurer.__hardware_case_r1_structurer_factory__ = True

__all__ = [
    "R1_AGENT_ID",
    "R1_EXTRACTION_CONTRACT_VERSION",
    "HARDWARE_R1_EXTRACTION_V2_SCHEMA",
    "HardwareCaseR1RuntimeStructurer",
    "R1_STAGE_A_AGENT_ID",
    "R1_STAGE_B_AGENT_ID",
    "R1_PIPELINE_VERSION",
    "HARDWARE_R1_STAGE_A_SCHEMA",
    "HARDWARE_R1_STAGE_B_SCHEMA",
    "HardwareCaseR1PipelineRuntime",
    "map_r1_runtime_error",
    "build_hardware_case_r1_structurer",
    "resolve_r1_runtime_paths",
]
