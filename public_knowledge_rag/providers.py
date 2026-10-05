from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Protocol

from .config import Settings
from .contracts import SearchHit


class ProviderUnavailable(RuntimeError):
    def __init__(self, message: str, code: str = "PROVIDER_UNREACHABLE"):
        self.code = code
        super().__init__(message)


class GenerationProvider(Protocol):
    provider_id: str

    def generate(self, question: str, contexts: list[SearchHit]) -> tuple[str, dict[str, object]]: ...


class OllamaProvider:
    provider_id = "ollama-remote-lan"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _request(self, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            self.settings.ollama_url + path,
            data=data,
            headers={"Content-Type": "application/json"} if data else {},
            method="POST" if data else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.settings.request_timeout_seconds) as response:
                return json.loads(response.read())
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise ProviderUnavailable("Remote Ollama provider unavailable; request failed closed.") from exc

    def version(self) -> dict[str, object]:
        return self._request("/api/version")

    def models(self) -> list[dict[str, object]]:
        result = self._request("/api/tags")
        models = result.get("models", [])
        if not isinstance(models, list):
            raise ProviderUnavailable("Remote Ollama returned an invalid model list.")
        return models

    def generate(self, question: str, contexts: list[SearchHit]) -> tuple[str, dict[str, object]]:
        model_entry = next((m for m in self.models() if m.get("name") == self.settings.ollama_model), None)
        if model_entry is None or (
            self.settings.ollama_model_digest
            and model_entry.get("digest") != self.settings.ollama_model_digest
        ):
            raise ProviderUnavailable("Pinned Ollama model tag or digest is unavailable; request failed closed.", "MODEL_UNAVAILABLE")
        prompt = (
            "Answer using only the public source excerpts below. If they do not answer the question, say so. "
            "Cite supporting excerpt markers such as [1].\n\n"
            + "\n\n".join(f"[{i}] {hit.text}" for i, hit in enumerate(contexts, 1))
            + "\n\nQuestion: " + question
        )
        result = self._request("/api/chat", {
            "model": self.settings.ollama_model,
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0, "num_predict": self.settings.max_generate_tokens},
            "think": self.settings.thinking_mode == "enabled",
        })
        message = result.get("message")
        answer = message.get("content") if isinstance(message, dict) else None
        if not isinstance(answer, str):
            raise ProviderUnavailable("Remote Ollama returned no answer content.")
        if not answer.strip():
            raise ProviderUnavailable("Remote Ollama returned no answer content (possibly truncated during reasoning).")
        snapshot = {
            "provider": self.provider_id,
            "model": self.settings.ollama_model,
            "digest": model_entry.get("digest"),
            "created_at": result.get("created_at"),
            "prompt_eval_count": result.get("prompt_eval_count"),
            "eval_count": result.get("eval_count"),
        }
        return answer, snapshot


class OpenAICompatibleProvider:
    """OpenAI-compatible Chat Completions and Responses API generation adapter."""

    provider_id = "openai_compatible"

    def __init__(self, settings: Settings, api_key: str | None):
        self.settings = settings
        self.api_key = api_key

    def _request(self, payload: dict[str, object]) -> dict[str, object]:
        if not self.api_key:
            raise ProviderUnavailable("OpenAI-compatible credential is missing.", "CREDENTIAL_MISSING")
        endpoint_suffix = "/chat/completions" if self.settings.openai_protocol == "chat_completions" else "/responses"
        req = urllib.request.Request(
            self.settings.openai_base_url.rstrip("/") + endpoint_suffix,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.settings.request_timeout_seconds) as response:
                decoded = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            status = exc.code
            if status in {401, 403}:
                raise ProviderUnavailable("OpenAI-compatible provider rejected the credential.", "AUTH_FAILED") from exc
            provider_code = ""
            try:
                error_body = json.loads(exc.read())
                error_value = error_body.get("error") if isinstance(error_body, dict) else None
                if isinstance(error_value, dict):
                    provider_code = str(error_value.get("code") or error_value.get("type") or "").lower()
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                pass
            if provider_code in {"model_not_found", "invalid_model", "model_unavailable"}:
                raise ProviderUnavailable("Configured model is unavailable from the provider.", "MODEL_UNAVAILABLE") from exc
            if status == 404:
                raise ProviderUnavailable("Selected protocol endpoint was not found.", "PROTOCOL_ERROR") from exc
            if status in {400, 405, 415, 422}:
                raise ProviderUnavailable("OpenAI-compatible request was rejected by the selected protocol.", "PROTOCOL_ERROR") from exc
            raise ProviderUnavailable("OpenAI-compatible provider request failed.", "PROVIDER_UNREACHABLE") from exc
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise ProviderUnavailable("OpenAI-compatible provider is unreachable; request failed closed.", "PROVIDER_UNREACHABLE") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderUnavailable("OpenAI-compatible provider returned invalid JSON.", "PROTOCOL_ERROR") from exc
        if not isinstance(decoded, dict):
            raise ProviderUnavailable("OpenAI-compatible provider returned an invalid response object.", "PROTOCOL_ERROR")
        return decoded

    @staticmethod
    def _prompt(question: str, contexts: list[SearchHit]) -> tuple[str, str]:
        system = (
            "Answer only from the provided public-source excerpts. If they do not answer the question, "
            "say that the answer cannot be determined from the supplied sources. Preserve source-backed "
            "citation markers such as [1]. Do not use outside knowledge."
        )
        excerpts = "\n\n".join(f"[{index}] {hit.text}" for index, hit in enumerate(contexts, 1))
        user = f"Public-source excerpts:\n{excerpts}\n\nQuestion: {question}"
        return system, user

    @staticmethod
    def _chat_text(response: dict[str, object]) -> str:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderUnavailable("Chat Completions response has no message choice.", "PROTOCOL_ERROR")
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            parts = [part.get("text", "") for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)]
            text = "".join(parts).strip()
        else:
            text = ""
        if not text:
            raise ProviderUnavailable("Chat Completions returned an empty message.", "EMPTY_RESPONSE")
        return text

    @staticmethod
    def _responses_text(response: dict[str, object]) -> str:
        direct = response.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        output = response.get("output")
        parts: list[str] = []
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for piece in content:
                    if isinstance(piece, dict) and piece.get("type") in {"output_text", "text"} and isinstance(piece.get("text"), str):
                        parts.append(piece["text"])
        text = "".join(parts).strip()
        if not text:
            raise ProviderUnavailable("Responses API returned no text output.", "EMPTY_RESPONSE")
        return text

    def _model_payload(self, question: str, contexts: list[SearchHit], *, test: bool = False) -> dict[str, object]:
        system, user = self._prompt(question, contexts)
        limit = min(self.settings.max_generate_tokens, 32) if test else self.settings.max_generate_tokens
        if self.settings.openai_protocol == "chat_completions":
            return {
                "model": self.settings.openai_model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": self.settings.temperature,
                "max_completion_tokens": limit,
            }
        return {
            "model": self.settings.openai_model,
            "instructions": system,
            "input": user,
            "temperature": self.settings.temperature,
            "max_output_tokens": limit,
        }

    def _parse(self, response: dict[str, object]) -> tuple[str, dict[str, object]]:
        if self.settings.openai_protocol == "chat_completions":
            answer = self._chat_text(response)
            usage = response.get("usage")
            snapshot_usage = {}
            if isinstance(usage, dict):
                snapshot_usage = {
                    "input_tokens": usage.get("prompt_tokens"),
                    "output_tokens": usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                }
        else:
            answer = self._responses_text(response)
            usage = response.get("usage")
            snapshot_usage = {}
            if isinstance(usage, dict):
                snapshot_usage = {
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                }
        snapshot = {
            "provider": self.provider_id,
            "protocol": self.settings.openai_protocol,
            "model": response.get("model") or self.settings.openai_model,
            **snapshot_usage,
        }
        return answer, snapshot

    def generate(self, question: str, contexts: list[SearchHit]) -> tuple[str, dict[str, object]]:
        response = self._request(self._model_payload(question, contexts))
        return self._parse(response)

    def test_connection(self) -> dict[str, object]:
        payload = self._model_payload("Reply with only OK.", [], test=True)
        response = self._request(payload)
        answer, snapshot = self._parse(response)
        return {"test_response_received": bool(answer), **snapshot}


class UnconfiguredManualProvider:
    """Explicit fail-closed adapter slot for future approved manual providers."""

    provider_id = "manual-provider-unconfigured"

    def generate(self, prompt: str) -> str:
        raise ProviderUnavailable("Manual evaluation provider is not configured for this infrastructure baseline.")
