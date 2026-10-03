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
    "invalidate_hardware_r1_stage_cache",
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
R1_PIPELINE_VERSION = "hardware-r1-agent-pipeline/v1.3.3"
R1_CACHE_KEY_VERSION = "v2"
R1_STAGE_A_VALIDATOR_VERSION = "hardware-r1-stage-a-validator/v1"
R1_STAGE_B_VALIDATOR_VERSION = "hardware-r1-stage-b-validator/v2"
R1_STAGE_A_SCHEMA_VERSION = "HardwareCaseR1CaseExtractionV13/v1"
R1_STAGE_B_SCHEMA_VERSION = "HardwareCaseR1ReusableKnowledgeV13/v1"

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
    """V2 cache for locally validated successful stage results only."""

    TABLE = "hardware_r1_stage_cache_v2"

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with _sqlite3.connect(self.db_path) as connection:
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.TABLE} (
                    stage TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    result_hash TEXT NOT NULL,
                    runtime_json TEXT NOT NULL,
                    validation_status TEXT NOT NULL,
                    validator_version TEXT NOT NULL,
                    pipeline_version TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    agent_config_hash TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    source_id TEXT,
                    source_run_id TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(stage, cache_key)
                )
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute(
                    f"PRAGMA table_info({self.TABLE})"
                ).fetchall()
            }
            if "source_id" not in columns:
                connection.execute(
                    f"ALTER TABLE {self.TABLE} ADD COLUMN source_id TEXT"
                )
            connection.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_hardware_r1_stage_cache_source
                ON {self.TABLE}(source_id)
                """
            )

    @staticmethod
    def _result_json(data: dict[str, Any]) -> str:
        return json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def _result_hash(cls, data: dict[str, Any]) -> str:
        return hashlib.sha256(cls._result_json(data).encode("utf-8")).hexdigest()

    def get(self, stage: str, cache_key: str) -> dict[str, Any] | None:
        with _sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                f"""
                SELECT result_json, result_hash, runtime_json,
                       validation_status, validator_version, pipeline_version,
                       schema_version, agent_config_hash, prompt_version,
                       source_run_id, created_at
                FROM {self.TABLE}
                WHERE stage=? AND cache_key=?
                """,
                (stage, cache_key),
            ).fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row[0])
            runtime = json.loads(row[2])
            if not isinstance(data, dict) or not isinstance(runtime, dict):
                raise ValueError("CACHE_JSON_OBJECT_REQUIRED")
        except Exception:
            return {
                "invalid": True,
                "reason": "CACHE_JSON_CORRUPTED",
                "created_at": row[10],
            }
        if str(row[3]) != "PASS":
            return {
                "invalid": True,
                "reason": "CACHE_VALIDATION_STATUS_INVALID",
                "created_at": row[10],
            }
        if self._result_hash(data) != str(row[1]):
            return {
                "invalid": True,
                "reason": "CACHE_RESULT_HASH_MISMATCH",
                "created_at": row[10],
            }
        return {
            "invalid": False,
            "data": data,
            "runtime": runtime,
            "result_hash": row[1],
            "validation_status": row[3],
            "validator_version": row[4],
            "pipeline_version": row[5],
            "schema_version": row[6],
            "agent_config_hash": row[7],
            "prompt_version": row[8],
            "source_run_id": row[9],
            "created_at": row[10],
        }

    def delete(self, stage: str, cache_key: str) -> bool:
        with _sqlite3.connect(self.db_path) as connection:
            cursor = connection.execute(
                f"DELETE FROM {self.TABLE} WHERE stage=? AND cache_key=?",
                (stage, cache_key),
            )
            return int(cursor.rowcount or 0) > 0

    def delete_source(self, source_id: str) -> int:
        value = str(source_id or "").strip()
        if not value:
            return 0
        with _sqlite3.connect(self.db_path) as connection:
            cursor = connection.execute(
                f"DELETE FROM {self.TABLE} WHERE source_id=?",
                (value,),
            )
            return int(cursor.rowcount or 0)

    def put(
        self,
        stage: str,
        cache_key: str,
        data: dict[str, Any],
        runtime_meta: dict[str, Any],
        *,
        source_id: str | None = None,
        validator_version: str,
        pipeline_version: str,
        schema_version: str,
        agent_config_hash: str,
        prompt_version: str,
    ) -> None:
        result_json = self._result_json(data)
        result_hash = hashlib.sha256(result_json.encode("utf-8")).hexdigest()
        with _sqlite3.connect(self.db_path) as connection:
            connection.execute(
                f"""
                INSERT INTO {self.TABLE}(
                    stage, cache_key, result_json, result_hash, runtime_json,
                    validation_status, validator_version, pipeline_version,
                    schema_version, agent_config_hash, prompt_version,
                    source_id, source_run_id, created_at
                ) VALUES (?, ?, ?, ?, ?, 'PASS', ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(stage, cache_key) DO UPDATE SET
                    result_json=excluded.result_json,
                    result_hash=excluded.result_hash,
                    runtime_json=excluded.runtime_json,
                    validation_status=excluded.validation_status,
                    validator_version=excluded.validator_version,
                    pipeline_version=excluded.pipeline_version,
                    schema_version=excluded.schema_version,
                    agent_config_hash=excluded.agent_config_hash,
                    prompt_version=excluded.prompt_version,
                    source_id=excluded.source_id,
                    source_run_id=excluded.source_run_id,
                    created_at=excluded.created_at
                """,
                (
                    stage,
                    cache_key,
                    result_json,
                    result_hash,
                    json.dumps(
                        runtime_meta,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    validator_version,
                    pipeline_version,
                    schema_version,
                    agent_config_hash,
                    prompt_version,
                    str(source_id or "").strip() or None,
                    str(runtime_meta.get("run_id") or "") or None,
                    _datetime.now(_timezone.utc).isoformat(),
                ),
            )

    def count(self, stage: str | None = None) -> int:
        query = f"SELECT COUNT(*) FROM {self.TABLE}"
        params: tuple[Any, ...] = ()
        if stage is not None:
            query += " WHERE stage=?"
            params = (stage,)
        with _sqlite3.connect(self.db_path) as connection:
            row = connection.execute(query, params).fetchone()
        return int((row or [0])[0] or 0)


def _canonical_payload_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stage_cache_key_v2(
    *,
    stage: str,
    source_id: str,
    input_hash_name: str,
    input_hash: str,
    agent_config_hash: str,
    prompt_version: str,
    pipeline_version: str,
    validator_version: str,
    schema_version: str,
) -> str:
    material = {
        "cache_key_version": R1_CACHE_KEY_VERSION,
        "stage": str(stage),
        "source_id": str(source_id),
        str(input_hash_name): str(input_hash),
        "agent_config_hash": str(agent_config_hash),
        "prompt_version": str(prompt_version),
        "pipeline_version": str(pipeline_version),
        "validator_version": str(validator_version),
        "schema_version": str(schema_version),
    }
    return _canonical_payload_hash(material)

def _payload_size(value: Any) -> tuple[int, int]:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return len(encoded), len(encoded.encode("utf-8"))


def _attempt_result_class(record: dict[str, Any]) -> tuple[str, str | None, Any]:
    status = str(record.get("status") or "").upper()
    error = record.get("error")
    error = error if isinstance(error, dict) else {}
    raw_error_code = str(error.get("code") or "").strip() or None
    category = str(error.get("category") or "").upper()
    details = error.get("details")
    details = details if isinstance(details, dict) else {}
    message = str(error.get("message") or "")
    timeout_material = " ".join(
        [
            str(raw_error_code or ""),
            message,
            str(details.get("exception_type") or ""),
            str(details.get("exception") or ""),
        ]
    ).casefold()
    http_status = (
        details.get("http_status")
        if details.get("http_status") is not None
        else details.get("status_code")
        if details.get("status_code") is not None
        else details.get("http_status_code")
    )
    if status == "COMPLETED":
        return "SUCCESS", raw_error_code, http_status
    if category == "VALIDATION":
        return "VALIDATION_ERROR", raw_error_code, http_status
    if category == "TRANSPORT":
        if "timeout" in timeout_material or "timed out" in timeout_material:
            return "TIMEOUT", raw_error_code, http_status
        return "TRANSPORT_ERROR", raw_error_code, http_status
    return "UNKNOWN", raw_error_code, http_status


def _attempt_metrics(runtime_db: Path, task_id: str) -> dict[str, Any]:
    call_ms: list[int] = []
    provider_attempts: list[dict[str, Any]] = []
    validation_cycles: dict[int, int] = {}
    prompt_tokens: list[int] = []
    completion_tokens: list[int] = []
    initial_call_count = 0
    transport_retry_count = 0
    observed_provider_calls = 0
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
        observed_provider_calls += 1
        duration_ms: int | None = None
        started = record.get("started_at")
        completed = record.get("completed_at")
        if started and completed:
            try:
                start_dt = _datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                end_dt = _datetime.fromisoformat(str(completed).replace("Z", "+00:00"))
                duration_ms = max(
                    0,
                    int((end_dt - start_dt).total_seconds() * 1000),
                )
                call_ms.append(duration_ms)
            except Exception:
                pass
        step_attempt = int(record.get("step_attempt_no") or 1)
        cycle = int(record.get("validation_cycle_no") or 1)
        transport_attempt = int(record.get("transport_attempt_no") or 1)
        validation_cycles[step_attempt] = max(
            validation_cycles.get(step_attempt, 1),
            cycle,
        )
        if transport_attempt > 1:
            transport_retry_count += 1
        elif cycle == 1:
            initial_call_count += 1

        result_class, raw_error_code, http_status = _attempt_result_class(record)
        provider_attempts.append(
            {
                "provider_call_seq": int(
                    record.get("provider_call_seq")
                    or record.get("attempt_no")
                    or observed_provider_calls
                ),
                "result_class": result_class,
                "duration_ms": duration_ms,
                "step_attempt_no": step_attempt,
                "validation_cycle_no": cycle,
                "transport_attempt_no": transport_attempt,
                "raw_error_code": raw_error_code,
                "http_status": http_status,
            }
        )

        metrics = record.get("execution_metrics") or {}
        if isinstance(metrics.get("prompt_tokens"), int):
            prompt_tokens.append(int(metrics["prompt_tokens"]))
        if isinstance(metrics.get("completion_tokens"), int):
            completion_tokens.append(int(metrics["completion_tokens"]))

    return {
        "provider_call_ms": call_ms,
        "provider_attempts": provider_attempts,
        "observed_provider_call_count": observed_provider_calls,
        "initial_call_count": initial_call_count,
        "transport_retry_count": transport_retry_count,
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
        schema_version: str,
        validator_version: str,
        input_hash_name: str,
        cache: _R1StageCache,
        stage_name: str,
        environ: Mapping[str, str],
    ):
        self.paths = paths
        self.stage_name = stage_name
        self.expected_agent_id = expected_agent_id
        self.schema_version = schema_version
        self.validator_version = validator_version
        self.input_hash_name = input_hash_name
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

    @property
    def prompt_version(self) -> str:
        return str(
            self.resolved.prompt.version or self.resolved.prompt.content_hash
        )

    def _cache_key(self, *, source_id: str, input_hash: str) -> str:
        return _stage_cache_key_v2(
            stage=self.stage_name,
            source_id=source_id,
            input_hash_name=self.input_hash_name,
            input_hash=input_hash,
            agent_config_hash=self.resolved.config_hash,
            prompt_version=self.prompt_version,
            pipeline_version=R1_PIPELINE_VERSION,
            validator_version=self.validator_version,
            schema_version=self.schema_version,
        )

    def _cache_identity_matches(self, cached: dict[str, Any]) -> bool:
        return (
            cached.get("validator_version") == self.validator_version
            and cached.get("pipeline_version") == R1_PIPELINE_VERSION
            and cached.get("schema_version") == self.schema_version
            and cached.get("agent_config_hash") == self.resolved.config_hash
            and cached.get("prompt_version") == self.prompt_version
            and cached.get("validation_status") == "PASS"
        )

    def _runtime_meta(
        self,
        result: Any,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        attempts = _attempt_metrics(self.paths["runtime_db"], result.task_id)
        input_chars, input_bytes = _payload_size(payload)
        if isinstance(result.data, dict):
            output_chars, output_bytes = _payload_size(result.data)
        else:
            output_chars, output_bytes = 0, 0
        error = getattr(result, "error", None)
        execution = getattr(result, "execution", None)
        return {
            "run_id": result.run_id,
            "task_id": result.task_id,
            "agent_id": result.agent_id,
            "agent_config_version": self.resolved.definition.version,
            "agent_config_hash": self.resolved.config_hash,
            "prompt_version": self.prompt_version,
            "provider_call_count": int(getattr(execution, "provider_calls", 0) or 0),
            "observed_provider_call_count": attempts["observed_provider_call_count"],
            "initial_call_count": attempts["initial_call_count"],
            "transport_retry_count": attempts["transport_retry_count"],
            "provider_call_ms": attempts["provider_call_ms"],
            "provider_attempts": attempts["provider_attempts"],
            "input_chars": input_chars,
            "input_bytes": input_bytes,
            "output_chars": output_chars,
            "output_bytes": output_bytes,
            "prompt_tokens": attempts["prompt_tokens"],
            "completion_tokens": attempts["completion_tokens"],
            "validation_retry_count": attempts["validation_retry_count"],
            "retry_budget_exhausted": bool(
                getattr(execution, "retry_budget_exhausted", False)
            ),
            "raw_error_code": str(getattr(error, "code", "") or "") or None,
        }

    def run(
        self,
        payload: dict[str, Any],
        *,
        source_id: str,
        input_hash: str,
        force_retry: bool = False,
        bypass_cache: bool = False,
        cache_rejected_by_validator: bool = False,
        require_cache_hit: bool = False,
        execution_mode: str | None = None,
    ) -> dict[str, Any]:
        cache_key = self._cache_key(source_id=source_id, input_hash=input_hash)
        input_chars, input_bytes = _payload_size(payload)
        rejected = bool(cache_rejected_by_validator)
        rejection_reason: str | None = (
            "CACHE_REJECTED_BY_VALIDATOR" if rejected else None
        )

        if not force_retry and not bypass_cache:
            cached = self.cache.get(self.stage_name, cache_key)
            if cached is not None:
                if cached.get("invalid") or not self._cache_identity_matches(cached):
                    self.cache.delete(self.stage_name, cache_key)
                    rejected = True
                    rejection_reason = str(
                        cached.get("reason") or "CACHE_IDENTITY_INVALID"
                    )
                else:
                    runtime_meta = dict(cached["runtime"])
                    runtime_meta.update(
                        {
                            "cache_hit": True,
                            "cache_rejected_by_validator": False,
                            "cache_rejection_reason": None,
                            "cached_from_run_id": (
                                cached.get("source_run_id")
                                or runtime_meta.get("run_id")
                            ),
                            "execution_mode": "CACHE_HIT",
                            "provider_call_count": 0,
                            "initial_call_count": 0,
                            "transport_retry_count": 0,
                            "provider_call_ms": [],
                            "provider_attempts": [],
                            "input_chars": input_chars,
                            "input_bytes": input_bytes,
                            "output_chars": _payload_size(cached["data"])[0],
                            "output_bytes": _payload_size(cached["data"])[1],
                            "validation_retry_count": 0,
                            "cache_recovery_call_count": 0,
                            "force_retry_call_count": 0,
                            "prompt_tokens": "UNKNOWN",
                            "completion_tokens": "UNKNOWN",
                            "cache_key_version": R1_CACHE_KEY_VERSION,
                            self.input_hash_name: input_hash,
                        }
                    )
                    return {
                        "ok": True,
                        "data": cached["data"],
                        "runtime": runtime_meta,
                        "cache_key": cache_key,
                        "source_id": source_id,
                        "cache_write_pending": False,
                    }

        if require_cache_hit:
            return {
                "ok": False,
                "data": None,
                "error_code": "STAGE_LAST_GOOD_CACHE_REQUIRED",
                "raw_error_code": None,
                "runtime": {
                    "run_id": None,
                    "task_id": None,
                    "agent_id": self.expected_agent_id,
                    "agent_config_version": self.resolved.definition.version,
                    "agent_config_hash": self.resolved.config_hash,
                    "prompt_version": self.prompt_version,
                    "execution_mode": "CACHE_REQUIRED_MISS",
                    "provider_call_count": 0,
                    "initial_call_count": 0,
                    "transport_retry_count": 0,
                    "validation_retry_count": 0,
                    "cache_recovery_call_count": 0,
                    "force_retry_call_count": 0,
                    "provider_call_ms": [],
                    "provider_attempts": [],
                    "input_chars": input_chars,
                    "input_bytes": input_bytes,
                    "output_chars": 0,
                    "output_bytes": 0,
                    "prompt_tokens": "UNKNOWN",
                    "completion_tokens": "UNKNOWN",
                    "cache_hit": False,
                    "cache_rejected_by_validator": rejected,
                    "cache_rejection_reason": rejection_reason,
                    "cached_from_run_id": None,
                    "cache_key_version": R1_CACHE_KEY_VERSION,
                    self.input_hash_name: input_hash,
                },
                "cache_key": cache_key,
                "source_id": source_id,
                "cache_write_pending": False,
            }

        mode = "force" if force_retry else ("recover" if bypass_cache or rejected else "run")
        base_request_id = (
            f"hardware-r1-v133:{self.stage_name.lower()}:{cache_key[:24]}"
        )
        request_id = f"{base_request_id}:{mode}:{_uuid4().hex[:12]}"

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
                        "cache_key_version": R1_CACHE_KEY_VERSION,
                        self.input_hash_name: input_hash,
                        "force_retry": bool(force_retry),
                        "cache_recovery": bool(bypass_cache or rejected),
                        "input_contract": payload.get("input_contract"),
                    },
                )
            )

        try:
            result = invoke(request_id)
        except _ExistingTaskNotCompleteError:
            result = invoke(f"{base_request_id}:retry:{_uuid4().hex[:12]}")

        runtime_meta = self._runtime_meta(result, payload)
        effective_mode = str(
            execution_mode
            or (
                "FORCE_FULL_RUN"
                if force_retry
                else "CACHE_RECOVERY"
                if bypass_cache or rejected
                else "PROVIDER_RUN"
            )
        )
        provider_calls = int(runtime_meta.get("provider_call_count") or 0)
        runtime_meta.update(
            {
                "execution_mode": effective_mode,
                "cache_recovery_call_count": (
                    provider_calls if effective_mode == "CACHE_RECOVERY" else 0
                ),
                "force_retry_call_count": (
                    provider_calls if effective_mode == "FORCE_FULL_RUN" else 0
                ),
                "cache_hit": False,
                "cache_rejected_by_validator": rejected,
                "cache_rejection_reason": rejection_reason,
                "cached_from_run_id": None,
                "cache_key_version": R1_CACHE_KEY_VERSION,
                self.input_hash_name: input_hash,
            }
        )
        if result.status == RuntimeStatus.COMPLETED and isinstance(result.data, dict):
            # Runtime/schema completion is only a cache candidate. The Pipeline
            # commits it after the current local stage validator passes.
            return {
                "ok": True,
                "data": result.data,
                "runtime": runtime_meta,
                "cache_key": cache_key,
                "source_id": source_id,
                "cache_write_pending": True,
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
            "source_id": source_id,
            "cache_write_pending": False,
        }

    def commit_success(self, stage_result: dict[str, Any]) -> bool:
        if not stage_result.get("ok") or not stage_result.get("cache_write_pending"):
            return False
        data = stage_result.get("data")
        runtime_meta = dict(stage_result.get("runtime") or {})
        cache_key = str(stage_result.get("cache_key") or "")
        source_id = str(stage_result.get("source_id") or "")
        if not isinstance(data, dict) or not cache_key or not source_id:
            return False
        self.cache.put(
            self.stage_name,
            cache_key,
            data,
            runtime_meta,
            source_id=source_id,
            validator_version=self.validator_version,
            pipeline_version=R1_PIPELINE_VERSION,
            schema_version=self.schema_version,
            agent_config_hash=self.resolved.config_hash,
            prompt_version=self.prompt_version,
        )
        stage_result["cache_write_pending"] = False
        stage_result["cache_committed"] = True
        return True

    def reject_cache(self, stage_result: dict[str, Any]) -> bool:
        cache_key = str(stage_result.get("cache_key") or "")
        if not cache_key:
            return False
        return self.cache.delete(self.stage_name, cache_key)

class HardwareCaseR1PipelineRuntime:
    """V1.3 two-stage Runtime with V2 validation-gated stage cache."""

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
    ):
        env = os.environ if environ is None else environ
        self.paths = _v13_paths(root=root, environ=env)
        self.paths["runtime_db"].parent.mkdir(parents=True, exist_ok=True)
        self.cache = _R1StageCache(self.paths["runtime_db"])
        self.stage_a = _R1StageRunner(
            paths=self.paths,
            config_key="stage_a_config",
            expected_agent_id=R1_STAGE_A_AGENT_ID,
            schema_ref=R1_STAGE_A_SCHEMA_REF,
            schema_version=R1_STAGE_A_SCHEMA_VERSION,
            validator_version=R1_STAGE_A_VALIDATOR_VERSION,
            input_hash_name="markdown_hash",
            cache=self.cache,
            stage_name="STAGE_A",
            environ=env,
        )
        self.stage_b = _R1StageRunner(
            paths=self.paths,
            config_key="stage_b_config",
            expected_agent_id=R1_STAGE_B_AGENT_ID,
            schema_ref=R1_STAGE_B_SCHEMA_REF,
            schema_version=R1_STAGE_B_SCHEMA_VERSION,
            validator_version=R1_STAGE_B_VALIDATOR_VERSION,
            input_hash_name="stage_b_input_hash",
            cache=self.cache,
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
        bypass_cache: bool = False,
        cache_rejected_by_validator: bool = False,
        require_cache_hit: bool = False,
        execution_mode: str | None = None,
    ) -> dict[str, Any]:
        return self.stage_a.run(
            payload,
            source_id=source_id,
            input_hash=markdown_hash,
            force_retry=force_retry,
            bypass_cache=bypass_cache,
            cache_rejected_by_validator=cache_rejected_by_validator,
            require_cache_hit=require_cache_hit,
            execution_mode=execution_mode,
        )

    def run_stage_b(
        self,
        payload: dict[str, Any],
        *,
        source_id: str,
        markdown_hash: str | None = None,
        force_retry: bool = False,
        bypass_cache: bool = False,
        cache_rejected_by_validator: bool = False,
        require_cache_hit: bool = False,
        execution_mode: str | None = None,
    ) -> dict[str, Any]:
        stage_b_input_hash = _canonical_payload_hash(payload)
        return self.stage_b.run(
            payload,
            source_id=source_id,
            input_hash=stage_b_input_hash,
            force_retry=force_retry,
            bypass_cache=bypass_cache,
            cache_rejected_by_validator=cache_rejected_by_validator,
            require_cache_hit=require_cache_hit,
            execution_mode=execution_mode,
        )

    def commit_stage_success(
        self,
        stage: str,
        stage_result: dict[str, Any],
    ) -> bool:
        if stage == "STAGE_A":
            return self.stage_a.commit_success(stage_result)
        if stage == "STAGE_B":
            return self.stage_b.commit_success(stage_result)
        raise ValueError("R1_STAGE_INVALID")

    def invalidate_source_cache(self, source_id: str) -> int:
        return self.cache.delete_source(source_id)

    def reject_stage_cache(
        self,
        stage: str,
        stage_result: dict[str, Any],
    ) -> bool:
        if stage == "STAGE_A":
            return self.stage_a.reject_cache(stage_result)
        if stage == "STAGE_B":
            return self.stage_b.reject_cache(stage_result)
        raise ValueError("R1_STAGE_INVALID")

def invalidate_hardware_r1_stage_cache(
    source_id: str,
    *,
    root: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Invalidate only cache rows belonging to one R1 source.

    This touches the stage cache only. Runtime Task/Run/Step/Attempt audit rows
    are intentionally preserved.
    """
    env = os.environ if environ is None else environ
    root_path = Path(root).resolve() if root is not None else package_root()
    runtime_raw = str(env.get("HARDWARE_CASE_RUNTIME_DB") or "").strip()
    runtime_db = (
        _resolved_path(root_path, runtime_raw)
        if runtime_raw
        else (root_path / DEFAULT_RUNTIME_DB).resolve()
    )
    if not runtime_db.exists():
        return 0
    return _R1StageCache(runtime_db).delete_source(source_id)


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
    "R1_CACHE_KEY_VERSION",
    "R1_STAGE_A_VALIDATOR_VERSION",
    "R1_STAGE_B_VALIDATOR_VERSION",
    "R1_STAGE_A_SCHEMA_VERSION",
    "R1_STAGE_B_SCHEMA_VERSION",
    "HARDWARE_R1_STAGE_A_SCHEMA",
    "HARDWARE_R1_STAGE_B_SCHEMA",
    "HardwareCaseR1PipelineRuntime",
    "map_r1_runtime_error",
    "build_hardware_case_r1_structurer",
    "resolve_r1_runtime_paths",
]
