#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Qwen / OpenAI-Compatible LLM Diagnostic

Usage:
  python tools/test_llm_qwen.py
  python tools/test_llm_qwen.py --config config/model.yaml
  python tools/test_llm_qwen.py --verbose

Purpose:
  1. Load config/model.yaml
  2. Validate model / base_url / api_key_env
  3. GET /models
  4. POST /chat/completions with a minimal prompt
  5. Test strict JSON output
  6. Detect Qwen-style reasoning_content/content behavior
  7. Print raw HTTP error body instead of hiding it behind "AI接口调用失败"

This script never prints the API key itself.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:
    print("[FATAL] PyYAML 未安装。执行: pip install pyyaml")
    raise


def normalize_base(base_url: str) -> str:
    return (base_url or "").strip().rstrip("/")


def chat_url(base_url: str) -> str:
    base = normalize_base(base_url)
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def models_url(base_url: str) -> str:
    base = normalize_base(base_url)
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if base.endswith("/v1"):
        return base + "/models"
    return base + "/v1/models"


def read_config(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    ai = data.get("ai") or {}
    return data, ai


def mask(s: str, keep: int = 4) -> str:
    if not s:
        return "<EMPTY>"
    if len(s) <= keep * 2:
        return "*" * len(s)
    return s[:keep] + "..." + s[-keep:]


def http_json(
    method: str,
    url: str,
    *,
    api_key: str = "",
    payload: dict[str, Any] | None = None,
    timeout: int = 30,
) -> tuple[int, dict[str, Any] | None, str]:
    headers = {"Accept": "application/json"}
    body = None

    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(url, data=body, method=method, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                obj = json.loads(raw)
            except Exception:
                obj = None
            return resp.status, obj, raw

    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            obj = json.loads(raw)
        except Exception:
            obj = None
        return exc.code, obj, raw

    except urllib.error.URLError as exc:
        return -1, None, f"URLError: {exc}"

    except Exception as exc:
        return -2, None, f"{type(exc).__name__}: {exc}"


def print_section(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def inspect_message(obj: dict[str, Any] | None) -> dict[str, Any]:
    out = {
        "has_choices": False,
        "has_message": False,
        "content": None,
        "reasoning_content": None,
        "finish_reason": None,
        "model": None,
    }
    if not isinstance(obj, dict):
        return out

    out["model"] = obj.get("model")

    choices = obj.get("choices")
    if not isinstance(choices, list) or not choices:
        return out

    out["has_choices"] = True
    c0 = choices[0] or {}
    out["finish_reason"] = c0.get("finish_reason")
    msg = c0.get("message")

    if not isinstance(msg, dict):
        return out

    out["has_message"] = True
    out["content"] = msg.get("content")
    out["reasoning_content"] = msg.get("reasoning_content")
    return out


def run_chat_test(
    name: str,
    cfg: dict[str, Any],
    api_key: str,
    *,
    payload_extra: dict[str, Any] | None = None,
    verbose: bool = False,
) -> bool:
    print_section(name)

    payload = {
        "model": cfg["model"],
        "messages": [
            {
                "role": "system",
                "content": "You are a diagnostic test. Return only the requested answer.",
            },
            {
                "role": "user",
                "content": 'Return exactly this JSON and nothing else: {"ok":true,"source":"qwen_test"}',
            },
        ],
        "temperature": 0,
    }

    # Start with the same parameter style used by the current Engine.
    if cfg.get("max_tokens") is not None:
        payload["max_tokens"] = int(cfg.get("max_tokens", 128))

    if payload_extra:
        payload.update(payload_extra)

    status, obj, raw = http_json(
        "POST",
        chat_url(cfg["base_url"]),
        api_key=api_key,
        payload=payload,
        timeout=int(cfg.get("timeout_seconds", 30)),
    )

    print("HTTP:", status)
    print("Endpoint:", chat_url(cfg["base_url"]))
    print("Payload keys:", sorted(payload.keys()))

    if status < 200 or status >= 300:
        print("[FAILED] HTTP request failed")
        print("Raw error body:")
        print(raw[:4000])
        return False

    info = inspect_message(obj)

    print("Response model:", info["model"])
    print("finish_reason:", info["finish_reason"])
    print("content:", repr(info["content"]))
    print("reasoning_content present:", bool(info["reasoning_content"]))

    if verbose:
        print("\nRaw response:")
        print(json.dumps(obj, ensure_ascii=False, indent=2)[:12000])

    if not info["has_choices"]:
        print("[FAILED] Response has no choices[0]")
        return False

    if not info["has_message"]:
        print("[FAILED] choices[0].message missing")
        return False

    # Important for Qwen3 / reasoning models.
    if (info["content"] is None or str(info["content"]).strip() == "") and info["reasoning_content"]:
        print("[WARNING] content 为空，但 reasoning_content 有内容。")
        print("          当前 Engine 只读取 message.content，可能因此把正常回复当失败。")
        return False

    if info["content"] is None:
        print("[FAILED] message.content is null")
        return False

    content = str(info["content"]).strip()

    # Strip common markdown fences for diagnosis only.
    cleaned = content
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        parsed = json.loads(cleaned)
        print("JSON parse: OK", parsed)
    except Exception as exc:
        print("[WARNING] JSON parse failed:", exc)
        print("Response text:", content[:3000])
        return False

    print("[PASS] Chat + JSON output works")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/model.yaml")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path

    print_section("1. CONFIG")

    if not config_path.exists():
        print("[FAILED] Config not found:", config_path)
        return 2

    _, ai = read_config(config_path)

    print("Config:", config_path)
    print("enabled:", ai.get("enabled"))
    print("provider:", ai.get("provider"))
    print("base_url:", ai.get("base_url"))
    print("model:", ai.get("model"))
    print("api_key_env:", ai.get("api_key_env"))
    print("timeout_seconds:", ai.get("timeout_seconds"))
    print("max_tokens:", ai.get("max_tokens"))

    required = ["base_url", "model", "api_key_env"]
    missing = [k for k in required if not str(ai.get(k, "")).strip()]

    if missing:
        print("[FAILED] Missing config:", missing)
        return 2

    env_name = str(ai["api_key_env"])
    api_key = os.getenv(env_name, "").strip()

    print("API key env exists:", bool(api_key))
    if api_key:
        print("API key masked:", mask(api_key))

    if not api_key:
        print(f"[FAILED] 环境变量 {env_name} 未设置")
        return 2

    print_section("2. GET /models")

    status, obj, raw = http_json(
        "GET",
        models_url(ai["base_url"]),
        api_key=api_key,
        timeout=min(int(ai.get("timeout_seconds", 30)), 30),
    )

    print("HTTP:", status)
    print("Endpoint:", models_url(ai["base_url"]))

    model_ids: list[str] = []

    if 200 <= status < 300 and isinstance(obj, dict):
        data = obj.get("data")
        if isinstance(data, list):
            model_ids = [
                str(x.get("id"))
                for x in data
                if isinstance(x, dict) and x.get("id")
            ]
        print("Models:", model_ids[:50])

        configured_model = str(ai["model"])
        if model_ids:
            if configured_model in model_ids:
                print("[PASS] configured model exists")
            else:
                print("[WARNING] model.yaml 配置的模型不在 /models 返回列表中:")
                print("          configured =", configured_model)
    else:
        print("[WARNING] /models unavailable. Raw response:")
        print(raw[:3000])

    # Test 1: exactly how Engine calls it today.
    engine_style_ok = run_chat_test(
        "3. ENGINE-STYLE CHAT TEST (max_tokens)",
        ai,
        api_key,
        verbose=args.verbose,
    )

    # Test 2: no max_tokens. This distinguishes Qwen/serving parameter mismatch.
    no_max_cfg = dict(ai)
    no_max_cfg.pop("max_tokens", None)

    no_max_ok = run_chat_test(
        "4. COMPATIBILITY TEST (without max_tokens)",
        no_max_cfg,
        api_key,
        verbose=args.verbose,
    )

    # Test 3: try response_format JSON object. Some OpenAI-compatible Qwen endpoints support it.
    json_mode_ok = run_chat_test(
        "5. JSON MODE TEST (response_format)",
        no_max_cfg,
        api_key,
        payload_extra={"response_format": {"type": "json_object"}},
        verbose=args.verbose,
    )

    print_section("6. DIAGNOSIS SUMMARY")

    print("Engine-style:", "PASS" if engine_style_ok else "FAIL")
    print("Without max_tokens:", "PASS" if no_max_ok else "FAIL")
    print("JSON mode:", "PASS" if json_mode_ok else "FAIL")

    if engine_style_ok:
        print("\nConclusion:")
        print("LLM 基础调用正常。若 Knowledge AI 仍失败，重点排查 Engine 的")
        print("Response Normalizer / DTO Validation / stage prompt，而不是 Qwen 网络/API。")
        return 0

    if not engine_style_ok and no_max_ok:
        print("\nLikely cause:")
        print("当前 Qwen/OpenAI-Compatible 服务对 max_tokens 参数兼容有问题。")
        print("当前 Engine builder/ai_client.py 会固定发送 max_tokens。")
        print("建议针对该 Provider 调整参数或增加兼容 fallback。")
        return 3

    print("\nLikely causes to inspect:")
    print("1. model.yaml 的 model 名称和 Qwen 服务实际模型 ID 不一致")
    print("2. Qwen 服务不是 /v1/chat/completions 兼容端点")
    print("3. Authorization / API Key 方式不兼容")
    print("4. Qwen reasoning 模型只返回 reasoning_content，content 为空")
    print("5. 服务端拒绝 max_tokens / response_format 等参数")
    print("6. HTTP body 已在上面打印，请以服务端真实错误为准")
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
