from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List
import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.request


class EmbeddingClientError(RuntimeError):
    pass


@dataclass
class EmbeddingResponse:
    vector: List[float]
    model: str


def _normalize(values: List[float]) -> List[float]:
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        return values
    return [value / norm for value in values]


class LocalHashEmbeddingClient:
    """Deterministic offline embedding for pipeline verification and local baseline."""

    def __init__(self, dimensions: int = 256, model: str = "local-hash-v1") -> None:
        self.dimensions = int(dimensions)
        self.model = model

    def embed(self, text: str) -> EmbeddingResponse:
        vector = [0.0] * self.dimensions
        normalized = re.sub(r"\s+", " ", text.strip().lower())
        tokens = re.findall(r"[\u4e00-\u9fff]|[a-z0-9_]+", normalized)
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        return EmbeddingResponse(vector=_normalize(vector), model=self.model)


class OpenAICompatibleEmbeddingClient:
    def __init__(self, config: dict) -> None:
        self.base_url = str(config.get("base_url", "")).rstrip("/")
        self.model = str(config.get("model", "")).strip()
        self.api_key_env = str(config.get("api_key_env", "REPEAT_CASE_API_KEY"))
        self.timeout = int(config.get("timeout_seconds", 120))
        self.max_retries = int(config.get("max_retries", 2))

    def embed(self, text: str) -> EmbeddingResponse:
        if not self.base_url:
            raise EmbeddingClientError("Embedding base_url未配置")
        if not self.model:
            raise EmbeddingClientError("Embedding model未配置")
        key = os.getenv(self.api_key_env, "").strip()
        if not key:
            raise EmbeddingClientError(f"环境变量{self.api_key_env}未设置")

        url = self.base_url
        if not url.endswith("/embeddings"):
            url = url + ("/embeddings" if url.endswith("/v1") else "/v1/embeddings")
        payload = json.dumps({"model": self.model, "input": text}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                vector = [float(v) for v in raw["data"][0]["embedding"]]
                return EmbeddingResponse(vector=vector, model=str(raw.get("model") or self.model))
            except (urllib.error.URLError, urllib.error.HTTPError, KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                break
        raise EmbeddingClientError(f"Embedding接口调用失败: {last_error}")


def create_embedding_client(config: dict):
    provider = str(config.get("provider", "local_hash")).strip().lower()
    if provider == "local_hash":
        return LocalHashEmbeddingClient(
            dimensions=int(config.get("dimensions") or 256),
            model=str(config.get("model") or "local-hash-v1"),
        )
    if provider == "openai_compatible":
        return OpenAICompatibleEmbeddingClient(config)
    raise EmbeddingClientError(f"不支持的Embedding provider: {provider}")
