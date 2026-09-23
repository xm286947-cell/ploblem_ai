"""Unified Runtime integration for the Reverse Quality single-issue agent.

Business owns the prompt/schema/result semantics. Unified Agent Runtime owns
provider execution, retry, timeout, call budget, configuration and secrets.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from runtime import AgentRequest, RuntimeStatus, SqliteTaskStore
from runtime.config import AgentConfigLoader, ConfiguredAgentRuntime


AGENT_ID = "reverse_quality.single_issue.analyze"
DEFAULT_AGENT_CONFIG = "config/runtime/agents/reverse_quality.single_issue.analyze.yaml"
DEFAULT_MODEL_CONFIG = "config/runtime/model.yaml"


class ReverseQualityAIField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class ReverseQualityQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str = ""
    reason: str = ""
    question: str = ""
    evidence_needed: list[str] | str = Field(default_factory=list)


class ReverseQualityAIResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: dict[str, ReverseQualityAIField] = Field(default_factory=dict)
    lifecycle_code: str = ""
    activity_code: str = ""
    match_reason: str = ""
    missing_condition: str = ""
    questions: list[ReverseQualityQuestion | str] = Field(default_factory=list)


@dataclass(frozen=True)
class ReverseQualityRuntimeResult:
    data: dict[str, Any]
    model: str
    task_id: str
    run_id: str
    provider_calls: int
    execution_snapshot_id: str


class ReverseQualityRuntimeError(ValueError):
    pass


def _configured_path(root: Path, env_names: tuple[str, ...], default: str) -> Path:
    for name in env_names:
        raw = os.environ.get(name, "").strip()
        if raw:
            path = Path(raw).expanduser()
            return path if path.is_absolute() else path.resolve()
    return root / default


class ReverseQualityRuntimeExecutor:
    """Thin domain entry over the canonical Runtime config path."""

    def __init__(
        self,
        root: str | Path,
        task_store_path: str | Path,
        *,
        model_config_path: str | Path | None = None,
        agent_config_path: str | Path | None = None,
        environ=None,
    ) -> None:
        self.root = Path(root).resolve()
        self.task_store_path = Path(task_store_path)
        self.task_store_path.parent.mkdir(parents=True, exist_ok=True)

        model_config = (
            Path(model_config_path)
            if model_config_path is not None
            else _configured_path(
                self.root,
                ("REVERSE_QUALITY_MODEL_CONFIG", "RUNTIME_MODEL_CONFIG"),
                DEFAULT_MODEL_CONFIG,
            )
        )
        agent_config = (
            Path(agent_config_path)
            if agent_config_path is not None
            else self.root / DEFAULT_AGENT_CONFIG
        )

        self.loader = AgentConfigLoader(
            root=self.root,
            model_profiles=model_config,
            schemas={"ReverseQualityAIResult": ReverseQualityAIResult},
            environ=os.environ if environ is None else environ,
        )
        self.store = SqliteTaskStore(self.task_store_path)
        self.runtime = ConfiguredAgentRuntime(self.store, config_loader=self.loader)
        self.resolved = self.runtime.load_agent(agent_config)

        if self.resolved.definition.agent_id != AGENT_ID:
            raise ReverseQualityRuntimeError(
                f"REVERSE_QUALITY_AGENT_ID_MISMATCH:{self.resolved.definition.agent_id}"
            )

    def execute(self, payload: dict[str, Any], *, request_id: str) -> ReverseQualityRuntimeResult:
        result = self.runtime.invoke(
            AgentRequest(
                request_id=request_id,
                agent_id=AGENT_ID,
                input=payload,
                metadata={"business_domain": "REVERSE_QUALITY"},
            )
        )
        if result.status != RuntimeStatus.COMPLETED:
            error = result.error
            code = error.code if error else "RUNTIME_EXECUTION_FAILED"
            category = error.category.value if error else "UNKNOWN"
            message = error.message if error else "Reverse Quality Runtime execution failed"
            raise ReverseQualityRuntimeError(f"{code}:{category}:{message}")

        if not isinstance(result.data, dict):
            raise ReverseQualityRuntimeError("REVERSE_QUALITY_RUNTIME_RESULT_INVALID")

        return ReverseQualityRuntimeResult(
            data=result.data,
            model=str(result.execution.model or self.resolved.provider.model or ""),
            task_id=result.task_id,
            run_id=result.run_id,
            provider_calls=int(result.execution.provider_calls or 0),
            execution_snapshot_id=str(result.execution.execution_snapshot_id or ""),
        )


__all__ = [
    "AGENT_ID",
    "ReverseQualityAIField",
    "ReverseQualityQuestion",
    "ReverseQualityAIResult",
    "ReverseQualityRuntimeResult",
    "ReverseQualityRuntimeError",
    "ReverseQualityRuntimeExecutor",
]
