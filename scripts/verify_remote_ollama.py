from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request


base = os.getenv("OLLAMA_URL", "http://192.168.1.100:11434").rstrip("/")
model = os.getenv("OLLAMA_MODEL", "qwen3-vl:8b-thinking-q4_K_M")
expected_digest = os.getenv("OLLAMA_MODEL_DIGEST", "901cae73216286ea8c5aba8b46d307ff7188f737285ec500c795a12f05225d28")


def get(path: str) -> dict[str, object]:
    with urllib.request.urlopen(base + path, timeout=5) as response:
        return json.load(response)


def main() -> int:
    started = time.monotonic()
    version = get("/api/version")
    tags = get("/api/tags")
    models = tags.get("models", [])
    actual = next((m for m in models if m.get("name") == model), None)
    if actual is None or actual.get("digest") != expected_digest:
        print(json.dumps({"result": "FAIL", "gate": "OLLAMA_REMOTE_MODEL_LIST", "expected_model": model,
                          "expected_digest": expected_digest, "actual": actual, "models": models}, ensure_ascii=False))
        return 1
    body = json.dumps({"model": model, "stream": False, "messages": [{"role": "user", "content": "Reply with exactly: infra probe ok"}],
                       "options": {"temperature": 0, "num_predict": 512}, "think": False}).encode()
    req = urllib.request.Request(base + "/api/chat", data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as response:
        inference = json.load(response)
    message = inference.get("message", {})
    if not isinstance(message, dict) or not isinstance(message.get("content"), str) or not message["content"].strip():
        print(json.dumps({"result": "FAIL", "gate": "OLLAMA_REMOTE_INFERENCE", "error": "empty response",
                          "done_reason": inference.get("done_reason"), "thinking_chars": len(message.get("thinking", "")) if isinstance(message, dict) else 0}))
        return 1
    print(json.dumps({"result": "PASS", "endpoint": base, "version": version.get("version"), "model": model,
                      "digest": actual.get("digest"), "response": message["content"],
                      "prompt_tokens": inference.get("prompt_eval_count"), "output_tokens": inference.get("eval_count"),
                      "latency_seconds": round(time.monotonic() - started, 2)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        print(json.dumps({"result": "FAIL", "gate": "OLLAMA_REMOTE_CONNECTIVITY", "error": type(exc).__name__}))
        raise SystemExit(1)
