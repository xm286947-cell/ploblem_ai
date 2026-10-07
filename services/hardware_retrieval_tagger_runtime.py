"""Unified Runtime binding for Hardware AI Retrieval W1.

This module is a thin product-side adapter only. Provider selection, retry,
validation, secret handling, and persistence remain owned by Unified Runtime.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from runtime import AgentRequest, RuntimeStatus, SqliteTaskStore
from runtime.config import AgentConfigLoader, ConfiguredAgentRuntime
from services.hardware_retrieval_metadata import ALLOWED_TAG_SOURCE_FIELDS


AGENT_ID = "hardware_retrieval.tag"
DEFAULT_AGENT_CONFIG = "config/runtime/agents/hardware_retrieval.tag.yaml"
DEFAULT_LOCAL_MODEL_CONFIG = "config/runtime/model.local.yaml"

TAG_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["term", "kind", "source_term", "source_fields"],
    "properties": {
        "term": {"type": "string", "minLength": 1},
        "kind": {"type": "string", "enum": ["FACT", "NORMALIZED", "EXPANSION"]},
        "source_term": {"type": "string", "minLength": 1},
        "source_fields": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {
                "type": "string",
                "enum": list(ALLOWED_TAG_SOURCE_FIELDS),
            },
        },
    },
    "additionalProperties": False,
}

HARDWARE_RETRIEVAL_TAGGER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["tags"],
    "properties": {
        "tags": {
            "type": "array",
            "maxItems": 128,
            "items": TAG_ITEM_SCHEMA,
        }
    },
    "additionalProperties": False,
}


class HardwareRetrievalTaggerRuntimeError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


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

    model_raw = str(
        env.get("HARDWARE_RETRIEVAL_MODEL_CONFIG")
        or env.get("HARDWARE_CASE_MODEL_CONFIG")
        or ""
    ).strip()
    model_path = (
        _resolved_path(root_path, model_raw)
        if model_raw
        else root_path / DEFAULT_LOCAL_MODEL_CONFIG
    )
    if not model_path.is_file():
        raise HardwareRetrievalTaggerRuntimeError("MODEL_LOCAL_CONFIG_REQUIRED")

    agent_raw = str(env.get("HARDWARE_RETRIEVAL_AGENT_CONFIG") or "").strip()
    agent_path = (
        _resolved_path(root_path, agent_raw)
        if agent_raw
        else root_path / DEFAULT_AGENT_CONFIG
    )
    if not agent_path.is_file():
        raise HardwareRetrievalTaggerRuntimeError("AGENT_CONFIG_REQUIRED")

    db_raw = str(env.get("HARDWARE_RETRIEVAL_RUNTIME_DB") or "").strip()
    runtime_db = (
        _resolved_path(root_path, db_raw)
        if db_raw
        else root_path / "data/runtime/hardware_retrieval_runtime.db"
    )
    return {
        "root": root_path,
        "model_config": model_path.resolve(),
        "agent_config": agent_path.resolve(),
        "runtime_db": runtime_db.resolve(),
    }


class HardwareRetrievalTaggerRuntimeInvoker:
    """Callable injected into HardwareRetrievalTagger."""

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.environ = os.environ if environ is None else environ
        self.paths = resolve_runtime_paths(root=root, environ=self.environ)
        self.paths["runtime_db"].parent.mkdir(parents=True, exist_ok=True)

        self.loader = AgentConfigLoader(
            root=self.paths["root"],
            model_profiles=self.paths["model_config"],
            schemas={"HardwareRetrievalTaggerOutput": HARDWARE_RETRIEVAL_TAGGER_SCHEMA},
            environ=self.environ,
        )
        self.store = SqliteTaskStore(self.paths["runtime_db"])
        self.runtime = ConfiguredAgentRuntime(
            self.store,
            config_loader=self.loader,
        )
        self.resolved = self.runtime.load_agent(self.paths["agent_config"])
        if self.resolved.definition.agent_id != AGENT_ID:
            raise HardwareRetrievalTaggerRuntimeError("AGENT_ID_MISMATCH")

    @staticmethod
    def _request_id(payload: Mapping[str, Any]) -> str:
        stable = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return "hardware-retrieval-tag:" + hashlib.sha256(stable).hexdigest()[:24]

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise HardwareRetrievalTaggerRuntimeError("TAGGER_INPUT_OBJECT_REQUIRED")

        result = self.runtime.invoke(
            AgentRequest(
                request_id=self._request_id(payload),
                agent_id=AGENT_ID,
                input=payload,
                metadata={
                    "business_domain": "HARDWARE_CASE",
                    "pipeline_stage": "RETRIEVAL_METADATA_TAGGING",
                    "formal_knowledge_write": False,
                },
            )
        )
        if result.status != RuntimeStatus.COMPLETED:
            error = getattr(result, "error", None)
            code = str(getattr(error, "code", None) or "RUNTIME_EXECUTION_FAILED")
            raise HardwareRetrievalTaggerRuntimeError(code)
        if not isinstance(result.data, dict):
            raise HardwareRetrievalTaggerRuntimeError("RUNTIME_OUTPUT_INVALID")
        return result.data


def build_hardware_retrieval_tagger_runtime() -> HardwareRetrievalTaggerRuntimeInvoker:
    return HardwareRetrievalTaggerRuntimeInvoker()


build_hardware_retrieval_tagger_runtime.__hardware_retrieval_tagger_runtime_factory__ = True


__all__ = [
    "AGENT_ID",
    "HARDWARE_RETRIEVAL_TAGGER_SCHEMA",
    "HardwareRetrievalTaggerRuntimeError",
    "HardwareRetrievalTaggerRuntimeInvoker",
    "build_hardware_retrieval_tagger_runtime",
    "resolve_runtime_paths",
]
