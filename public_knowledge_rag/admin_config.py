from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import UI_CONFIG_FIELDS, Settings, env_sources
from .secret_store import SecretStore


class ConfigValidationError(ValueError):
    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("Configuration validation failed.")


class ConfigurationAdmin:
    """Local UI settings and test-token management; provider keys live separately."""

    def __init__(self, base: Settings | None = None, config_path: Path | None = None, secret_path: Path | None = None):
        self.base = base or Settings.load_base()
        self.validate({key: getattr(self.base, key) for key in UI_CONFIG_FIELDS}, partial=True)
        configured_path = os.getenv("PKR_UI_CONFIG_PATH")
        self.config_path = config_path or Path(configured_path or (self.base.data_dir / "config.local.json"))
        self.secret_store = SecretStore(self.base.data_dir) if secret_path is None else SecretStore(secret_path.parent)
        if secret_path is not None:
            self.secret_store.path = secret_path
        self.saved = self._read_saved()
        self.effective = Settings.with_overrides(self.base, self.saved)
        self._tested: dict[str, tuple[str, str, bool, float]] = {}

    def _read_saved(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}
        try:
            value = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Local Public Knowledge config is unreadable; last-known-good settings cannot be loaded.") from exc
        if not isinstance(value, dict) or set(value) - set(UI_CONFIG_FIELDS):
            raise RuntimeError("Local Public Knowledge config contains unsupported fields.")
        return self.validate(value, partial=True)

    @staticmethod
    def _validate_url(value: Any, field: str, errors: dict[str, str], *, required: bool) -> str:
        raw = str(value or "").strip().rstrip("/")
        if not raw and not required:
            return ""
        parts = urlsplit(raw)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            errors[field] = "Enter a complete http:// or https:// provider URL."
        elif parts.username or parts.password or parts.query or parts.fragment:
            errors[field] = "Provider URLs cannot contain credentials, query strings, or fragments."
        elif len(raw) > 500:
            errors[field] = "Provider URL is too long."
        return raw

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

        provider_type = str(payload.get("provider_type", "ollama")).strip().lower()
        if provider_type not in {"ollama", "openai_compatible"}:
            errors["provider_type"] = "Choose a supported provider type."
        result["provider_type"] = provider_type

        if "ollama_url" in payload:
            result["ollama_url"] = ConfigurationAdmin._validate_url(payload["ollama_url"], "ollama_url", errors, required=provider_type == "ollama")
        if "ollama_model" in payload:
            model = str(payload["ollama_model"] or "").strip()
            if provider_type == "ollama" and (not model or len(model) > 200 or any(ord(ch) < 32 for ch in model)):
                errors["ollama_model"] = "Enter a valid model name or tag."
            result["ollama_model"] = model

        if "ollama_model_digest" in payload:
            digest = str(payload["ollama_model_digest"] or "").strip().lower()
            if digest and (len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest)):
                errors["ollama_model_digest"] = "Model digest must be empty or a 64-character hexadecimal identity."
            if provider_type == "ollama" and not partial and not digest:
                errors["ollama_model_digest"] = "Ollama requires the configured pinned model digest."
            result["ollama_model_digest"] = digest

        if "openai_base_url" in payload:
            result["openai_base_url"] = ConfigurationAdmin._validate_url(payload["openai_base_url"], "openai_base_url", errors, required=provider_type == "openai_compatible")
        if "openai_protocol" in payload:
            protocol = str(payload["openai_protocol"] or "").strip().lower()
            if protocol not in {"chat_completions", "responses"}:
                errors["openai_protocol"] = "Choose chat_completions or responses explicitly."
            result["openai_protocol"] = protocol
        if "openai_model" in payload:
            model = str(payload["openai_model"] or "").strip()
            if provider_type == "openai_compatible" and (not model or len(model) > 200 or any(ord(ch) < 32 for ch in model)):
                errors["openai_model"] = "Enter the model name required by the compatible provider."
            result["openai_model"] = model

        if "request_timeout_seconds" in payload:
            try:
                timeout = float(payload["request_timeout_seconds"])
                if not math.isfinite(timeout) or not 1 <= timeout <= 600:
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

        if "temperature" in payload:
            try:
                temperature = float(payload["temperature"])
                if not math.isfinite(temperature) or not 0 <= temperature <= 2:
                    raise ValueError
                result["temperature"] = temperature
            except (ValueError, TypeError):
                errors["temperature"] = "Temperature must be between 0 and 2."

        if "thinking_mode" in payload:
            thinking = str(payload["thinking_mode"] or "").strip().lower()
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

    def effective_credential(self, *, candidate_key: str = "", clear_local: bool = False) -> str | None:
        return self.secret_store.effective_key(candidate_key=candidate_key, clear_local=clear_local)

    def record_provider_test(self, candidate: dict[str, Any], credential: str | None, clear_local: bool = False) -> str:
        test_id = secrets.token_urlsafe(24)
        fingerprint = self.secret_store.fingerprint(credential)
        self._tested[test_id] = (self.candidate_hash(candidate), fingerprint, clear_local, time.monotonic() + 600)
        return test_id

    def save(
        self,
        payload: dict[str, Any],
        test_id: str,
        *,
        candidate_key: str = "",
        clear_local: bool = False,
    ) -> str:
        candidate = self.validate(payload)
        if clear_local and candidate_key:
            raise ConfigValidationError({"api_key": "Enter a replacement key or clear the saved key, not both."})
        if clear_local and candidate["provider_type"] != "openai_compatible":
            raise ConfigValidationError({"clear_credential": "Credential clearing is available only for OpenAI Compatible."})
        if candidate_key and candidate["provider_type"] != "openai_compatible":
            raise ConfigValidationError({"api_key": "API Key is accepted only for OpenAI Compatible."})
        credential = self.effective_credential(candidate_key=candidate_key, clear_local=clear_local)
        if candidate["provider_type"] == "openai_compatible" and not credential:
            raise ConfigValidationError({"credential": "CREDENTIAL_MISSING: enter a key or configure OPENAI_COMPATIBLE_API_KEY."})

        tested = self._tested.pop(test_id, None)
        fingerprint = self.secret_store.fingerprint(credential)
        if (
            tested is None
            or tested[3] < time.monotonic()
            or tested[0] != self.candidate_hash(candidate)
            or tested[1] != fingerprint
            or tested[2] != clear_local
        ):
            raise ConfigValidationError({"provider_test": "Run Test Connection again for these exact settings and credential."})

        # Apply the credential/config pair with rollback if either file write
        # fails. The key remains in its independent mode-0600 secret store.
        persisted: dict[str, Any] = {}
        for key, value in candidate.items():
            if value != getattr(self.base, key):
                persisted[key] = value
        previous_local_key = self.secret_store.local_key()
        try:
            if clear_local:
                self.secret_store.clear()
            elif candidate_key:
                self.secret_store.write(candidate_key)
            self._write_config(persisted)
        except OSError:
            if previous_local_key:
                self.secret_store.write(previous_local_key)
            else:
                self.secret_store.clear()
            raise

        was_applied = all(getattr(self.effective, key) == value for key, value in candidate.items())
        self.saved = persisted
        return "APPLIED" if was_applied else "RESTART_REQUIRED"

    def _write_config(self, persisted: dict[str, Any]) -> None:
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

    def state(self, *, service_version: str, embedding_status: str, retrieval_adapter: str, active_credential: str | None = None) -> dict[str, Any]:
        env = env_sources()
        sources: dict[str, str] = {}
        for key in UI_CONFIG_FIELDS:
            sources[key] = "LOCAL_UI_OVERRIDE" if key in self.saved else env.get(key, "DEFAULT")
        sources["credential"] = self.secret_store.source()
        for key in ("chunk_size", "chunk_overlap"):
            sources[key] = env.get(key, "DEFAULT")

        resolved_data = self.effective.data_dir.expanduser().resolve()
        safe_data_identity = hashlib.sha256(str(resolved_data).encode("utf-8")).hexdigest()[:16]
        generation = self.effective.editable_snapshot()
        generation["credential_status"] = self._credential_status(self.effective.provider_type, active_credential)
        desired_credential = self.effective_credential()
        desired_status = self._credential_status(self.editable_values()["provider_type"], desired_credential)
        apply_state = self.apply_state()
        if self.effective.provider_type == "openai_compatible" and self.secret_store.fingerprint(desired_credential) != self.secret_store.fingerprint(active_credential):
            apply_state = "RESTART_REQUIRED"
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
            "saved_credential_status": desired_status,
            "apply_state": apply_state,
        }

    @staticmethod
    def _credential_status(provider_type: str, credential: str | None) -> str:
        if provider_type == "ollama":
            return "NOT_REQUIRED"
        return "CONFIGURED" if credential else "MISSING"

    def editable_values(self) -> dict[str, Any]:
        desired = Settings.with_overrides(self.base, self.saved)
        return {key: getattr(desired, key) for key in UI_CONFIG_FIELDS}

    def apply_state(self) -> str:
        desired = Settings.with_overrides(self.base, self.saved)
        return "APPLIED" if all(getattr(desired, key) == getattr(self.effective, key) for key in UI_CONFIG_FIELDS) else "RESTART_REQUIRED"
