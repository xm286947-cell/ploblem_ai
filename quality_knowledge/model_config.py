from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
import yaml


class ModelConfigError(ValueError):
    pass


def resolve_model_config_path(root: str | Path) -> Path:
    root = Path(root).resolve()
    override = os.getenv("QUALITY_ISSUE_MODEL_CONFIG", "").strip()
    if override:
        p = Path(override).expanduser()
        if not p.is_absolute():
            p = root / p
        return p.resolve()
    return (root / "config" / "model.yaml").resolve()


def load_quality_issue_ai_config(root: str | Path) -> tuple[dict[str, Any], Path]:
    path = resolve_model_config_path(root)
    if not path.exists():
        raise ModelConfigError(f"model.yaml不存在: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # quality_issue_ai may override the shared ai node; missing fields inherit from ai.
    base = dict(data.get("ai") or {})
    override = dict(data.get("quality_issue_ai") or {})
    cfg = {**base, **override}
    cfg["_config_path"] = str(path)
    return cfg, path


def validate_quality_issue_ai_config(root: str | Path, *, require_enabled: bool = True) -> dict[str, Any]:
    cfg, path = load_quality_issue_ai_config(root)
    errors: list[str] = []
    enabled = bool(cfg.get("enabled", False))
    if require_enabled and not enabled:
        errors.append("ai.enabled=false")
    provider = str(cfg.get("provider", "openai_compatible")).strip()
    base_url = str(cfg.get("base_url", "")).strip()
    model = str(cfg.get("model", "")).strip()
    api_key_env = str(cfg.get("api_key_env", "REPEAT_CASE_API_KEY")).strip()
    if provider == "openai_compatible":
        if not base_url:
            errors.append("ai.base_url未配置")
        if not model:
            errors.append("ai.model未配置")
        if not api_key_env:
            errors.append("ai.api_key_env未配置")
        elif not os.getenv(api_key_env, "").strip():
            errors.append(f"环境变量{api_key_env}未设置")
    return {
        "config_path": str(path),
        "enabled": enabled,
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "api_key_env": api_key_env,
        "api_key_present": bool(os.getenv(api_key_env, "").strip()) if api_key_env else False,
        "temperature": cfg.get("temperature", 0),
        "max_tokens": cfg.get("max_tokens", 4096),
        "timeout_seconds": cfg.get("timeout_seconds", 120),
        "max_retries": cfg.get("max_retries", 2),
        "errors": errors,
        "ok": not errors,
    }
