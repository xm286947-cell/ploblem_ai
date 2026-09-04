from __future__ import annotations

import json
import hashlib
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


def load_quality_issue_ai_config(root: str | Path, *, agent_id: str = "", stage: str = "") -> tuple[dict[str, Any], Path]:
    path = resolve_model_config_path(root)
    if not path.exists():
        raise ModelConfigError(f"model.yaml不存在: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # quality_issue_ai may override the shared ai node; missing fields inherit from ai.
    base = dict(data.get("ai") or {})
    override = dict(data.get("quality_issue_ai") or {})
    cfg = {**base, **override}
    agents = dict(data.get("quality_issue_agents") or {})
    explicit = str(agent_id or "").strip()
    selected = "" if explicit.upper() == "DEFAULT" else explicit
    if selected:
        if selected not in agents:
            raise ModelConfigError(f"智能体不存在: {selected}; config={path}")
        cfg.update(dict(agents[selected] or {}))
    cfg["_agent_id"] = selected or "DEFAULT"
    cfg["_config_path"] = str(path)
    return cfg, path


def validate_quality_issue_ai_config(root: str | Path, *, require_enabled: bool = True, agent_id: str = "", stage: str = "") -> dict[str, Any]:
    cfg, path = load_quality_issue_ai_config(root, agent_id=agent_id, stage=stage)
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
        "agent_id": cfg.get("_agent_id", "DEFAULT"),
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
        "proxy_url": cfg.get("proxy_url") or os.getenv("QUALITY_ISSUE_PROXY_URL", ""),
        "errors": errors,
        "ok": not errors,
    }


def list_quality_issue_agents(root: str | Path) -> dict[str, Any]:
    path = resolve_model_config_path(root)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    base = {**dict(data.get("ai") or {}), **dict(data.get("quality_issue_ai") or {})}
    configured = dict(data.get("quality_issue_agents") or {})
    items = [{
        "agent_id": "DEFAULT", "label": "默认智能体", "provider": base.get("provider"),
        "model": base.get("model"), "base_url": base.get("base_url"), "enabled": bool(base.get("enabled", False)),
    }]
    for key, value in configured.items():
        item = {**base, **dict(value or {})}
        items.append({
            "agent_id": str(key), "label": str(item.get("label") or key),
            "provider": item.get("provider"), "model": item.get("model"), "base_url": item.get("base_url"),
            "enabled": bool(item.get("enabled", False)),
        })
    enabled_agents=[x for x in items if x['agent_id']!='DEFAULT' and x['enabled']]
    signatures = {(x.get("base_url") or base.get('base_url'), x.get("model")) for x in enabled_agents}
    return {"items": items, "assignment_strategy": "ROUND_ROBIN_BY_ISSUE",
            "eligible_agent_ids": [x['agent_id'] for x in enabled_agents],
            "distinct_model_count": len(signatures), "config_path": str(path)}


def choose_quality_issue_agent(root: str | Path, knowledge_id: str, *, slot: int | None = None) -> str:
    """Choose one enabled agent for the whole issue; never split its four stages."""
    ids=list_quality_issue_agents(root)["eligible_agent_ids"]
    if not ids:
        return "DEFAULT"
    if slot is not None:
        return ids[int(slot) % len(ids)]
    digest=int(hashlib.sha256(str(knowledge_id).encode("utf-8")).hexdigest()[:16],16)
    return ids[digest % len(ids)]
