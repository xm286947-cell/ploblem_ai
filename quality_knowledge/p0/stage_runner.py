"""Production-capable native V2 stage runner backed by P0 prompt records."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from builder.ai_client import OpenAICompatibleClient
from quality_knowledge.models.analysis_v2 import OccurrenceAnalysisV2DTO
from quality_knowledge.response_normalizer_v2 import normalize_stage_v2
from runtime import (
    AgentConfigLoader,
    AgentRequest,
    ConfiguredAgentRuntime,
    RuntimeStatus,
    SqliteTaskStore,
)


@dataclass(frozen=True)
class StageExecutionResult:
    """Validated runner output plus replay/debug information for persistence."""

    payload: dict[str, Any]
    raw_response: str
    model_name: str | None
    debug: dict[str, Any]


class ProductionStageRunnerError(RuntimeError):
    """A production runner error carrying sanitized retry/validation diagnostics."""

    def __init__(self, code: str, debug: dict[str, Any]):
        self.code = code
        self.debug = debug
        super().__init__(code)


class ProductionV2StageRunner:
    """Runs a V2 stage through any OpenAI-compatible ``complete(messages)`` client.

    The prompt is always read from the initialized P0 database.  The runner can
    receive a test double, or create the project's OpenAICompatibleClient from
    a supplied configuration for production use.
    """

    def __init__(
        self,
        repository: Any,
        *,
        ai_client: Any | None = None,
        ai_config: dict[str, Any] | None = None,
        max_attempts: int = 2,
    ) -> None:
        if ai_client is None and ai_config is None:
            raise ProductionStageRunnerError("AI_CONFIGURATION_NOT_CONFIGURED", {"retry_errors": []})
        self.repository = repository
        self.ai_client = ai_client or OpenAICompatibleClient(ai_config or {})
        self.max_attempts = max(1, int(max_attempts))

    def run_stage(self, *, stage: str, context: Any) -> StageExecutionResult:
        prompt = self.repository.get_active_prompt(stage)
        messages = self._messages(prompt, context)
        retry_errors: list[str] = []
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.ai_client.complete(messages)
                content, model_name, raw = self._response_parts(response)
                payload = self._parse_json(stage, content)
                parsed = normalize_stage_v2(stage, payload)
                normalized = self._normalized_payload(stage, parsed)
                return StageExecutionResult(
                    payload=normalized,
                    raw_response=content,
                    model_name=model_name,
                    debug={
                        "attempt": attempt,
                        "retry_errors": retry_errors,
                        "prompt_version_id": prompt["prompt_version_id"],
                        "prompt_content_hash": prompt["content_hash"],
                        "client_raw": raw,
                    },
                )
            except Exception as error:
                retry_errors.append(str(error))
                if attempt == self.max_attempts:
                    raise ProductionStageRunnerError(
                        "V2_STAGE_RUNNER_FAILED",
                        {
                            "attempt": attempt,
                            "retry_errors": retry_errors,
                            "prompt_version_id": prompt.get("prompt_version_id"),
                        },
                    ) from error
        raise AssertionError("unreachable")

    @staticmethod
    def _user_input(prompt: dict[str, Any], context: Any) -> dict[str, Any]:
        return {
            "contract_version": "2.0.0",
            "stage": context.stage,
            "analysis_set_id": context.analysis_set_id,
            "knowledge_id": context.knowledge_id,
            "issue_version_id": context.issue_version_id,
            "input_hash": context.input_hash,
            "taxonomy_version_id": prompt["taxonomy_version_id"],
            "output_contract": prompt["output_contract_json"],
            "model_parameters": prompt["model_params_json"],
            "normalized_snapshot": context.normalized_snapshot,
            "raw_source": context.raw_json,
            "human_confirmation_context": context.human_confirmation_context,
            "completed_stages": context.completed_stages,
        }

    @staticmethod
    def _messages(prompt: dict[str, Any], context: Any) -> list[dict[str, str]]:
        user_input = ProductionV2StageRunner._user_input(prompt, context)
        return [
            {"role": "system", "content": prompt["prompt_text"]},
            {
                "role": "user",
                "content": json.dumps(user_input, ensure_ascii=False, sort_keys=True, default=str),
            },
        ]

    @staticmethod
    def _response_parts(response: Any) -> tuple[str, str | None, Any]:
        if isinstance(response, dict):
            content = response.get("content")
            model = response.get("model")
            raw = response.get("raw", response)
        else:
            content = getattr(response, "content", None)
            model = getattr(response, "model", None)
            raw = getattr(response, "raw", None)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("AI_RESPONSE_CONTENT_INVALID")
        return content, str(model) if model else None, raw

    @staticmethod
    def _parse_json(stage: str, content: str) -> dict[str, Any]:
        candidate = content.strip()
        if candidate.startswith("```"):
            candidate = candidate.split("\n", 1)[1] if "\n" in candidate else ""
            if candidate.endswith("```"):
                candidate = candidate[:-3]
        try:
            value = json.loads(candidate.strip())
        except json.JSONDecodeError as error:
            raise ValueError(f"V2_STAGE_JSON_INVALID:{stage}:{error.msg}") from error
        if not isinstance(value, dict):
            raise ValueError(f"V2_STAGE_JSON_OBJECT_REQUIRED:{stage}")
        return value

    @staticmethod
    def _normalized_payload(stage: str, parsed: Any) -> dict[str, Any]:
        if stage == "capability_gap":
            return {"capability_gaps": [item.model_dump(mode="json") for item in parsed]}
        return parsed.model_dump(mode="json")


class RuntimeConfiguredV2StageRunner:
    """Hybrid V2 runner with occurrence migrated to the canonical Runtime config.

    RCFG-02 intentionally migrates one real Major Issue agent only. The other
    V2 stages continue through the injected fallback runner until their own
    migration tasks are approved. The migrated occurrence path never creates a
    provider client or retry policy in business code.
    """

    AGENT_ID = "major_issue.v2.occurrence"
    AGENT_CONFIG = "config/runtime/agents/major_issue.v2.occurrence.yaml"

    def __init__(
        self,
        repository: Any,
        *,
        runtime: ConfiguredAgentRuntime,
        fallback_runner: Any,
    ) -> None:
        self.repository = repository
        self.runtime = runtime
        self.fallback_runner = fallback_runner
        self.resolved = self.runtime.load_agent(self.AGENT_CONFIG)

    @staticmethod
    def resolve_model_config(
        root: str | Path,
        model_config_path: str | Path | None = None,
    ) -> Path:
        """Resolve Runtime model config using the Storage-proven precedence.

        1. explicit caller path;
        2. MAJOR_MODEL_CONFIG selection;
        3. non-committed config/model.local.yaml when present;
        4. shared config/runtime/model.yaml fallback.

        Provider credentials remain a Runtime concern. The selected profile may
        use direct values or *_env references according to Runtime rules.
        """
        project_root = Path(root).resolve()
        selected: str | Path | None = model_config_path
        if selected is None:
            configured = os.environ.get("MAJOR_MODEL_CONFIG", "").strip()
            if configured:
                selected = configured

        if selected is not None:
            path = Path(selected).expanduser()
            if not path.is_absolute():
                path = project_root / path
            path = path.resolve()
            if not path.is_file():
                raise ValueError(
                    f"MAJOR_MODEL_CONFIG_NOT_FOUND:{path}"
                )
            return path

        local_path = project_root / "config/model.local.yaml"
        if local_path.is_file():
            return local_path.resolve()

        return (
            project_root / "config/runtime/model.yaml"
        ).resolve()

    @classmethod
    def from_project(
        cls,
        repository: Any,
        *,
        root: str | Path,
        runtime_db_path: str | Path,
        fallback_runner: Any,
        model_config_path: str | Path | None = None,
    ) -> "RuntimeConfiguredV2StageRunner":
        project_root = Path(root).resolve()
        resolved_model_config = cls.resolve_model_config(
            project_root,
            model_config_path,
        )
        loader = AgentConfigLoader(
            root=project_root,
            model_profiles=resolved_model_config,
            schemas={"OccurrenceAnalysisV2DTO": OccurrenceAnalysisV2DTO},
        )
        runtime = ConfiguredAgentRuntime(
            SqliteTaskStore(runtime_db_path),
            config_loader=loader,
        )
        runner = cls(
            repository,
            runtime=runtime,
            fallback_runner=fallback_runner,
        )
        runner.model_config_path = resolved_model_config
        return runner

    def run_stage(self, *, stage: str, context: Any) -> StageExecutionResult:
        if stage != "occurrence":
            return self.fallback_runner.run_stage(stage=stage, context=context)

        prompt = self.repository.get_active_prompt(stage)
        self._assert_prompt_contract(prompt)
        request_id = (
            f"major-issue-occurrence:{context.analysis_set_id}:{context.input_hash}"
        )
        result = self.runtime.invoke(
            AgentRequest(
                request_id=request_id,
                agent_id=self.AGENT_ID,
                input=ProductionV2StageRunner._user_input(prompt, context),
                metadata={
                    "business_domain": "MAJOR_ISSUE",
                    "analysis_set_id": context.analysis_set_id,
                    "stage": stage,
                },
            )
        )
        if result.status != RuntimeStatus.COMPLETED:
            error = result.error
            raise ProductionStageRunnerError(
                "V2_STAGE_RUNTIME_FAILED",
                {
                    "retry_errors": [
                        error.code if error is not None else str(result.status)
                    ],
                    "runtime_status": str(result.status),
                    "runtime_task_id": result.task_id,
                    "provider_calls": result.execution.provider_calls,
                    "prompt_version_id": prompt["prompt_version_id"],
                },
            )

        parsed = normalize_stage_v2(stage, result.data)
        payload = ProductionV2StageRunner._normalized_payload(stage, parsed)
        return StageExecutionResult(
            payload=payload,
            raw_response=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            model_name=self.resolved.definition.model,
            debug={
                "attempt": result.execution.provider_calls,
                "retry_errors": [],
                "prompt_version_id": prompt["prompt_version_id"],
                "prompt_content_hash": prompt["content_hash"],
                "runtime_task_id": result.task_id,
                "runtime_run_id": result.run_id,
                "runtime_execution_snapshot_id": (
                    result.execution.execution_snapshot_id
                ),
                "runtime_agent_id": self.AGENT_ID,
                "runtime_agent_config_hash": self.resolved.config_hash,
                "provider_calls": result.execution.provider_calls,
                "sdk_retry": 0,
            },
        )

    def _assert_prompt_contract(self, prompt: dict[str, Any]) -> None:
        configured_prompt = self.runtime.config_loader.read_prompt_text(self.resolved)
        if prompt.get("prompt_version_id") != self.resolved.prompt.version:
            raise ProductionStageRunnerError(
                "V2_RUNTIME_PROMPT_VERSION_MISMATCH",
                {
                    "retry_errors": [],
                    "active_prompt_version_id": prompt.get("prompt_version_id"),
                    "runtime_prompt_version": self.resolved.prompt.version,
                },
            )
        if prompt.get("prompt_text") != configured_prompt:
            raise ProductionStageRunnerError(
                "V2_RUNTIME_PROMPT_CONTENT_MISMATCH",
                {
                    "retry_errors": [],
                    "prompt_version_id": prompt.get("prompt_version_id"),
                    "runtime_prompt_hash": self.resolved.prompt.content_hash,
                },
            )
