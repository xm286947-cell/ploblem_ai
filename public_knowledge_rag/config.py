from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


UI_CONFIG_NAME = "config.local.json"
UI_CONFIG_FIELDS = (
    "provider_type",
    "ollama_url",
    "ollama_model",
    "ollama_model_digest",
    "openai_base_url",
    "openai_protocol",
    "openai_model",
    "request_timeout_seconds",
    "max_generate_tokens",
    "temperature",
    "thinking_mode",
)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    ollama_url: str
    ollama_model: str
    ollama_model_digest: str
    request_timeout_seconds: float
    max_generate_tokens: int
    max_source_bytes: int
    chunk_size: int
    chunk_overlap: int
    provider_type: str = "ollama"
    thinking_mode: str = "disabled"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_protocol: str = "chat_completions"
    openai_model: str = ""
    temperature: float = 0.0

    @classmethod
    def load_base(cls) -> "Settings":
        return cls(
            data_dir=Path(os.getenv("PKR_DATA_DIR", "./data")),
            ollama_url=os.getenv("OLLAMA_URL", "http://192.168.1.100:11434").rstrip("/"),
            ollama_model=os.getenv("OLLAMA_MODEL", "qwen3-vl:8b-thinking-q4_K_M"),
            ollama_model_digest=os.getenv("OLLAMA_MODEL_DIGEST", "901cae73216286ea8c5aba8b46d307ff7188f737285ec500c795a12f05225d28"),
            request_timeout_seconds=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "45")),
            max_generate_tokens=int(os.getenv("OLLAMA_NUM_PREDICT", "512")),
            max_source_bytes=int(os.getenv("MAX_SOURCE_BYTES", str(8 * 1024 * 1024))),
            chunk_size=int(os.getenv("CHUNK_SIZE", "1200")),
            chunk_overlap=int(os.getenv("CHUNK_OVERLAP", "120")),
            provider_type=os.getenv("GENERATION_PROVIDER", "ollama").strip().lower(),
            thinking_mode=os.getenv("OLLAMA_THINKING_MODE", "disabled").strip().lower(),
            openai_base_url=os.getenv("OPENAI_COMPATIBLE_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            openai_protocol=os.getenv("OPENAI_COMPATIBLE_PROTOCOL", "chat_completions").strip().lower(),
            openai_model=os.getenv("OPENAI_COMPATIBLE_MODEL", "").strip(),
            temperature=float(os.getenv("GENERATION_TEMPERATURE", "0")),
        )

    @classmethod
    def load(cls) -> "Settings":
        base = cls.load_base()
        path = Path(os.getenv("PKR_UI_CONFIG_PATH", str(base.data_dir / UI_CONFIG_NAME)))
        if not path.exists():
            return base
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Local Public Knowledge config is unreadable; refusing to start with ambiguous settings.") from exc
        if not isinstance(payload, dict) or set(payload) - set(UI_CONFIG_FIELDS):
            raise RuntimeError("Local Public Knowledge config has unsupported fields; refusing to start.")
        return cls.with_overrides(base, payload)

    @classmethod
    def with_overrides(cls, base: "Settings", overrides: dict[str, Any]) -> "Settings":
        return replace(base, **{key: value for key, value in overrides.items() if key in UI_CONFIG_FIELDS})

    def editable_snapshot(self) -> dict[str, object]:
        is_openai = self.provider_type == "openai_compatible"
        return {
            "provider_type": self.provider_type,
            "base_url": self.openai_base_url if is_openai else self.ollama_url,
            "model": self.openai_model if is_openai else self.ollama_model,
            "model_digest": self.ollama_model_digest if not is_openai else None,
            "timeout_seconds": self.request_timeout_seconds,
            "max_output_tokens": self.max_generate_tokens,
            "thinking_mode": self.thinking_mode if not is_openai else None,
            "openai_base_url": self.openai_base_url,
            "openai_protocol": self.openai_protocol,
            "openai_model": self.openai_model,
            "temperature": self.temperature,
            "credential_status": "NOT_REQUIRED" if not is_openai else "MISSING",
        }

    def public_snapshot(self) -> dict[str, object]:
        return {
            "service_version": "0.1.0",
            "provider_type": self.provider_type,
            "ollama_url": self.ollama_url,
            "ollama_model": self.ollama_model,
            "ollama_model_digest": self.ollama_model_digest,
            "request_timeout_seconds": self.request_timeout_seconds,
            "max_generate_tokens": self.max_generate_tokens,
            "thinking_mode": self.thinking_mode,
            "openai_base_url": self.openai_base_url,
            "openai_protocol": self.openai_protocol,
            "openai_model": self.openai_model,
            "temperature": self.temperature,
            "max_source_bytes": self.max_source_bytes,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "source_class_gate": "PUBLIC_ONLY",
            "provider_policy": self.provider_type.upper(),
        }

    def config_hash(self) -> str:
        payload = json.dumps(self.public_snapshot(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()


def env_sources() -> dict[str, str]:
    env_names = {
        "provider_type": "GENERATION_PROVIDER",
        "ollama_url": "OLLAMA_URL",
        "ollama_model": "OLLAMA_MODEL",
        "ollama_model_digest": "OLLAMA_MODEL_DIGEST",
        "request_timeout_seconds": "OLLAMA_TIMEOUT_SECONDS",
        "max_generate_tokens": "OLLAMA_NUM_PREDICT",
        "thinking_mode": "OLLAMA_THINKING_MODE",
        "openai_base_url": "OPENAI_COMPATIBLE_BASE_URL",
        "openai_protocol": "OPENAI_COMPATIBLE_PROTOCOL",
        "openai_model": "OPENAI_COMPATIBLE_MODEL",
        "temperature": "GENERATION_TEMPERATURE",
        "openai_api_key": "OPENAI_COMPATIBLE_API_KEY",
        "max_source_bytes": "MAX_SOURCE_BYTES",
        "chunk_size": "CHUNK_SIZE",
        "chunk_overlap": "CHUNK_OVERLAP",
    }
    return {field: ("ENV" if env_name in os.environ else "DEFAULT") for field, env_name in env_names.items()}
