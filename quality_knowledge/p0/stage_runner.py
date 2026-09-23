"""Production-capable native V2 stage runner backed by P0 prompt records."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from builder.ai_client import OpenAICompatibleClient
from quality_knowledge.response_normalizer_v2 import normalize_stage_v2


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
    def _messages(prompt: dict[str, Any], context: Any) -> list[dict[str, str]]:
        user_input = {
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
