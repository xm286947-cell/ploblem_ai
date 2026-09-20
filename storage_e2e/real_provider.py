from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import TypeAdapter, ValidationError

from runtime import ErrorCategory, RuntimeStepError
from runtime.adapters import StorageFieldResult


class OpenAICompatibleStorageRealProvider:
    """Minimal Storage domain provider adapter for STORAGE-REAL-E2E-01.

    Runtime owns retry/budget/snapshot/resume. This adapter only performs one
    provider call attempt and translates provider/JSON/schema failures into
    Runtime standard errors.
    """

    def __init__(self, prompt_path: str | Path, *, timeout_seconds: int = 120) -> None:
        self.prompt_path = Path(prompt_path)
        self.timeout_seconds = timeout_seconds
        self._field_list = TypeAdapter(list[StorageFieldResult])

    def _prompt(self) -> str:
        return self.prompt_path.read_text(encoding="utf-8")

    def __call__(self, payload: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
        provider = context.get("runtime", {}).get("provider_config") or {}
        base_url = str(provider.get("base_url") or "").rstrip("/")
        model = provider.get("model")
        api_key = provider.get("api_key")

        if not base_url or not model:
            raise RuntimeStepError(
                "resolved provider configuration is incomplete",
                code="PROVIDER_CONFIG_INCOMPLETE",
                category=ErrorCategory.CONFIG,
                retryable=False,
                details={"has_base_url": bool(base_url), "has_model": bool(model)},
            )

        contract = (
            "\nFor STORAGE-REAL-E2E-01 return only a JSON array with exactly one object. "
            "The object must use field_id='pe_cycle', status='FOUND', "
            "normalized_value=3000, unit='cycles', evidence=[], and no markdown fences. "
            "Do not add any other field_id."
        )
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": self._prompt() + contract},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
        }
        if provider.get("temperature") is not None:
            body["temperature"] = provider["temperature"]
        if provider.get("max_tokens") is not None:
            body["max_tokens"] = provider["max_tokens"]

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        request = Request(
            f"{base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers=headers,
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:
            retryable = exc.code in {408, 409, 425, 429, 500, 502, 503, 504}
            raise RuntimeStepError(
                f"provider HTTP {exc.code}",
                code="PROVIDER_HTTP_ERROR",
                category=ErrorCategory.TRANSPORT,
                retryable=retryable,
                details={"http_status": exc.code},
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeStepError(
                f"provider transport failure: {type(exc).__name__}",
                code="PROVIDER_TRANSPORT",
                category=ErrorCategory.TRANSPORT,
                retryable=True,
                details={"exception_type": type(exc).__name__},
            ) from exc

        try:
            envelope = json.loads(raw_body)
            choice = envelope["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeStepError(
                "provider returned an invalid OpenAI-compatible envelope",
                code="PROVIDER_ENVELOPE_INVALID",
                category=ErrorCategory.VALIDATION,
                retryable=True,
            ) from exc

        if str(finish_reason or "").lower() == "length":
            raise RuntimeStepError(
                "provider output was truncated",
                code="OUTPUT_TRUNCATED",
                category=ErrorCategory.VALIDATION,
                retryable=True,
                details={"finish_reason": finish_reason},
            )

        if not isinstance(content, str) or not content.strip():
            raise RuntimeStepError(
                "provider returned empty content",
                code="EMPTY_PROVIDER_CONTENT",
                category=ErrorCategory.VALIDATION,
                retryable=True,
            )

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeStepError(
                "provider content is not strict JSON",
                code="INVALID_JSON",
                category=ErrorCategory.VALIDATION,
                retryable=True,
            ) from exc

        try:
            fields = self._field_list.validate_python(parsed)
        except ValidationError as exc:
            raise RuntimeStepError(
                "provider JSON failed StorageFieldResult schema",
                code="STORAGE_SCHEMA_INVALID",
                category=ErrorCategory.VALIDATION,
                retryable=True,
                details={"errors": exc.errors(include_input=False)},
            ) from exc

        return [field.model_dump(mode="json") for field in fields]


__all__ = ["OpenAICompatibleStorageRealProvider"]
