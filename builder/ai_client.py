from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List
import json
import os
import time
import urllib.error
import urllib.request
import socket


class AIClientError(RuntimeError):
    pass


def _normalize_chat_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


@dataclass
class AIResponse:
    content: str
    model: str
    raw: Dict[str, Any]


class OpenAICompatibleClient:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.base_url = str(config.get("base_url", "")).strip()
        self.model = str(config.get("model", "")).strip()
        self.api_key_env = str(config.get("api_key_env", "REPEAT_CASE_API_KEY"))
        self.timeout = int(config.get("timeout_seconds", 120))
        self.max_retries = int(config.get("max_retries", 2))
        self.proxy_url = str(config.get("proxy_url") or os.getenv("QUALITY_ISSUE_PROXY_URL", "")).strip()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": self.proxy_url, "https": self.proxy_url})) if self.proxy_url else urllib.request.build_opener()

    def complete(self, messages: List[dict]) -> AIResponse:
        if not self.base_url:
            raise AIClientError("AI base_url未配置")
        if not self.model:
            raise AIClientError("AI model未配置")

        api_key = os.getenv(self.api_key_env, "").strip()
        if not api_key:
            raise AIClientError(f"环境变量{self.api_key_env}未设置")

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.config.get("temperature", 0),
            "max_tokens": self.config.get("max_tokens", 4096),
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            _normalize_chat_url(self.base_url),
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                choice = raw["choices"][0]
                content = choice["message"]["content"]
                finish_reason = choice.get("finish_reason")
                if finish_reason == "length":
                    raise AIClientError(
                        f"AI响应达到输出Token上限(max_tokens={payload['max_tokens']})，JSON可能被截断"
                    )
                return AIResponse(
                    content=str(content),
                    model=str(raw.get("model") or self.model),
                    raw=raw,
                )
            except (AIClientError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError, socket.timeout, KeyError, IndexError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                break
        if isinstance(last_error,(TimeoutError,socket.timeout)) or "timed out" in str(last_error).lower():
            raise AIClientError(f"AI接口调用超时({self.timeout}s, retries={self.max_retries}): {last_error}")
        raise AIClientError(f"AI接口调用失败: {last_error}")


class MockAIClient:
    def __init__(self, response_file: str | Path, model: str = "mock-model") -> None:
        self.response_file = Path(response_file)
        self.model = model

    def complete(self, messages: List[dict]) -> AIResponse:
        if not self.response_file.exists():
            raise AIClientError(f"Mock响应文件不存在: {self.response_file}")
        data = json.loads(self.response_file.read_text(encoding="utf-8"))
        return AIResponse(
            content=json.dumps(data, ensure_ascii=False),
            model=self.model,
            raw={"mock": True},
        )
