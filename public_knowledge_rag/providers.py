from __future__ import annotations

import json
import urllib.error
import urllib.request

from .config import Settings
from .contracts import SearchHit


class ProviderUnavailable(RuntimeError):
    pass


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
        if model_entry is None or model_entry.get("digest") != self.settings.ollama_model_digest:
            raise ProviderUnavailable("Pinned Ollama model tag or digest is unavailable; request failed closed.")
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
            "think": False,
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


class UnconfiguredManualProvider:
    """Explicit fail-closed adapter slot for future approved manual providers."""

    provider_id = "manual-provider-unconfigured"

    def generate(self, prompt: str) -> str:
        raise ProviderUnavailable("Manual evaluation provider is not configured for this infrastructure baseline.")
