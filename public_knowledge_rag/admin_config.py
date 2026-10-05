from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import UI_CONFIG_FIELDS, Settings, env_sources


class ConfigValidationError(ValueError):
    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("Configuration validation failed.")


class ConfigurationAdmin:
    """Local, non-secret configuration persistence and last-known-good handling."""

    def __init__(self, base: Settings | None = None, config_path: Path | None = None):
        self.base = base or Settings.load_base()
        self.validate({key: getattr(self.base, key) for key in UI_CONFIG_FIELDS})
        configured_path = os.getenv("PKR_UI_CONFIG_PATH")
        self.config_path = config_path or Path(configured_path or (self.base.data_dir / "config.local.json"))
        self.saved = self._read_saved()
        self.effective = Settings.with_overrides(self.base, self.saved)
        self._tested: dict[str, tuple[str, float]] = {}

    def _read_saved(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}
        try:
            value = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Local Public Knowledge config is unreadable; last-known-good settings cannot be loaded.") from exc
        if not isinstance(value, dict) or set(value) - set(UI_CONFIG_FIELDS):
            raise RuntimeError("Local Public Knowledge config contains unsupported fields.")
        normalized = self.validate(value, partial=True)
        return normalized

    @staticmethod
    def validate(payload: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
        errors: dict[str, str] = {}
        if not isinstance(payload, dict):
            raise ConfigValidationError({"config": "Configuration must be an object."})
        extra = set(payload) - set(UI_CONFIG_FIELDS)
        if extra:
            errors["config"] = "Unsupported configuration field(s)."
        if not partial and set(payload) != set(UI_CONFIG_FIELDS):
            errors["config"] = "The configuration is incomplete. Reload current values and try again."
        result = dict(payload)

        if "provider_type" in payload:
            provider_type = str(payload["provider_type"]).strip().lower()
            if provider_type != "ollama":
                errors["provider_type"] = "Only the currently supported Ollama provider can be configured."
            result["provider_type"] = provider_type

        if "ollama_url" in payload:
            raw_url = str(payload["ollama_url"]).strip().rstrip("/")
            parts = urlsplit(raw_url)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                errors["ollama_url"] = "Enter a complete http:// or https:// provider URL."
            elif parts.username or parts.password or parts.query or parts.fragment:
                errors["ollama_url"] = "Provider URLs cannot contain credentials, query strings, or fragments."
            elif len(raw_url) > 500:
                errors["ollama_url"] = "Provider URL is too long."
            result["ollama_url"] = raw_url

        if "ollama_model" in payload:
            model = str(payload["ollama_model"]).strip()
            if not model or len(model) > 200 or any(ord(ch) < 32 for ch in model):
                errors["ollama_model"] = "Enter a valid model name or tag."
            result["ollama_model"] = model

        if "ollama_model_digest" in payload:
            digest = str(payload["ollama_model_digest"]).strip().lower()
            if digest and not re.fullmatch(r"[0-9a-f]{64}", digest):
                errors["ollama_model_digest"] = "Model digest must be empty or a 64-character hexadecimal identity."
            result["ollama_model_digest"] = digest

        if "request_timeout_seconds" in payload:
            try:
                timeout = float(payload["request_timeout_seconds"])
                if not 1 <= timeout <= 600:
                    raise ValueError
                result["request_timeout_seconds"] = timeout
            except (ValueError, TypeError):
                errors["request_timeout_seconds"] = "Timeout must be between 1 and 600 seconds."

        if "max_generate_tokens" in payload:
            try:
                tokens = int(payload["max_generate_tokens"])
                if isinstance(payload["max_generate_tokens"], bool) or not 1 <= tokens <= 32768:
                    raise ValueError
                result["max_generate_tokens"] = tokens
            except (ValueError, TypeError):
                errors["max_generate_tokens"] = "Max output tokens must be between 1 and 32768."

        if "thinking_mode" in payload:
            thinking = str(payload["thinking_mode"]).strip().lower()
            if thinking not in {"disabled", "enabled"}:
                errors["thinking_mode"] = "Thinking mode must be disabled or enabled."
            result["thinking_mode"] = thinking

        if errors:
            raise ConfigValidationError(errors)
        return {key: result[key] for key in UI_CONFIG_FIELDS if key in result}

    @staticmethod
    def candidate_settings(base: Settings, candidate: dict[str, Any]) -> Settings:
        return Settings.with_overrides(base, candidate)

    @staticmethod
    def candidate_hash(candidate: dict[str, Any]) -> str:
        canonical = json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def record_provider_test(self, candidate: dict[str, Any]) -> str:
        test_id = secrets.token_urlsafe(24)
        self._tested[test_id] = (self.candidate_hash(candidate), time.monotonic() + 600)
        return test_id

    def save(self, payload: dict[str, Any], test_id: str) -> str:
        candidate = self.validate(payload)
        tested = self._tested.pop(test_id, None)
        if tested is None or tested[1] < time.monotonic() or tested[0] != self.candidate_hash(candidate):
            raise ConfigValidationError({"provider_test": "Run Test Connection successfully for these exact values before saving."})

        # Persist only values that differ from the environment/default layer. This
        # lets updated environment settings take effect after a local override is removed.
        persisted: dict[str, Any] = {}
        for key, value in candidate.items():
            base_value = getattr(self.base, key)
            if value != base_value:
                persisted[key] = value
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.config_path.with_name(self.config_path.name + ".tmp-" + secrets.token_hex(8))
        try:
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(persisted, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_path, self.config_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

        was_applied = all(getattr(self.effective, key) == value for key, value in candidate.items())
        self.saved = persisted
        return "APPLIED" if was_applied else "RESTART_REQUIRED"

    def state(self, *, service_version: str, embedding_status: str, retrieval_adapter: str) -> dict[str, Any]:
        env = env_sources()
        sources: dict[str, str] = {}
        for key in UI_CONFIG_FIELDS:
            if key in self.saved:
                sources[key] = "LOCAL_UI_OVERRIDE"
            else:
                sources[key] = env.get(key, "DEFAULT")
        for key in ("chunk_size", "chunk_overlap"):
            sources[key] = env.get(key, "DEFAULT")

        resolved_data = self.effective.data_dir.expanduser().resolve()
        safe_data_identity = hashlib.sha256(str(resolved_data).encode("utf-8")).hexdigest()[:16]
        generation = self.effective.editable_snapshot()
        return {
            "current_effective_config": {
                "generation": generation,
                "embedding": {
                    "provider": "unconfigured",
                    "model": None,
                    "runtime": "not configured",
                    "endpoint": None,
                    "status": embedding_status,
                },
                "retrieval": {
                    "mode": "LEXICAL",
                    "adapter": retrieval_adapter,
                    "top_k": 10,
                    "chunk_size": self.effective.chunk_size,
                    "chunk_overlap": self.effective.chunk_overlap,
                    "vector_backend": "NONE",
                    "reranker": "OFF",
                },
                "service": {
                    "service_version": service_version,
                    "data_directory_id": safe_data_identity,
                    "source_class_gate": "PUBLIC_ONLY",
                    "config_hash": self.effective.config_hash(),
                    "capabilities": ["LIVE", "FIXTURE_REPLAY"],
                    "health": "ok",
                },
                "sources": sources,
            },
            "saved_config": self.editable_values(),
            "apply_state": self.apply_state(),
        }

    def editable_values(self) -> dict[str, Any]:
        desired = Settings.with_overrides(self.base, self.saved)
        return {key: getattr(desired, key) for key in UI_CONFIG_FIELDS}

    def apply_state(self) -> str:
        desired = Settings.with_overrides(self.base, self.saved)
        return "APPLIED" if all(getattr(desired, key) == getattr(self.effective, key) for key in UI_CONFIG_FIELDS) else "RESTART_REQUIRED"
