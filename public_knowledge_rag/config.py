from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path


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

    @classmethod
    def load(cls) -> "Settings":
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
        )

    def public_snapshot(self) -> dict[str, object]:
        return {
            "service_version": "0.1.0",
            "ollama_url": self.ollama_url,
            "ollama_model": self.ollama_model,
            "ollama_model_digest": self.ollama_model_digest,
            "request_timeout_seconds": self.request_timeout_seconds,
            "max_generate_tokens": self.max_generate_tokens,
            "max_source_bytes": self.max_source_bytes,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "source_class_gate": "PUBLIC_ONLY",
            "provider_policy": "REMOTE_OLLAMA_LAN",
        }

    def config_hash(self) -> str:
        payload = json.dumps(self.public_snapshot(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()


settings = Settings.load()
