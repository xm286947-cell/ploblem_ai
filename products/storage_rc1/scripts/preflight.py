from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import yaml

EXPECTED = "f9ca45f82960b3ce380273cf26868bc842a72b7f"
MODEL_REF = "qwen_prod"
PLACEHOLDER_TOKENS = {
    "__SET_YOUR_DASHSCOPE_API_KEY__",
    "__REPLACE_IN_YOUR_LOCAL_COPY_ONLY__",
    "__REPLACE_LOCALLY_FOR_TEST_ONLY__",
    "__SET_ME__",
    "__SET_YOUR_AGENT_API_KEY__",
}


def _safe_model_config(path: Path) -> dict:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("model config root must be a mapping")
    return raw


def _resolve_real_profile(config_path: Path) -> tuple[dict, str, str | None, str]:
    raw = _safe_model_config(config_path)
    models = raw.get("models") or {}
    if not isinstance(models, dict) or MODEL_REF not in models:
        raise ValueError(f"model config missing models.{MODEL_REF}")
    profile = models[MODEL_REF]
    if not isinstance(profile, dict):
        raise ValueError(f"models.{MODEL_REF} must be a mapping")

    base_url = str(profile.get("base_url") or "").strip()
    base_url_env = str(profile.get("base_url_env") or "").strip()
    if not base_url and base_url_env:
        base_url = os.environ.get(base_url_env, "").strip()

    api_key = profile.get("api_key")
    api_key_env = str(profile.get("api_key_env") or "").strip()
    if (api_key is None or str(api_key).strip() == "") and api_key_env:
        api_key = os.environ.get(api_key_env)
    key_text = str(api_key or "").strip()
    if key_text in PLACEHOLDER_TOKENS:
        key_text = ""

    source = "direct" if profile.get("base_url") else f"env:{base_url_env or '<missing>'}"
    return profile, base_url, (key_text or None), source


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-root", default=os.getenv("UNIFIED_AGENT_RUNTIME_ROOT", ""))
    ap.add_argument("--mode", choices=["mock", "real"], default="mock")
    args = ap.parse_args()

    root = Path(args.runtime_root).expanduser().resolve() if args.runtime_root else None
    errors: list[str] = []
    notes: list[str] = []

    if root is None or not (root / "runtime" / "__init__.py").exists():
        errors.append("UNIFIED_AGENT_RUNTIME_ROOT 未指向有效 Runtime 仓库")
    else:
        # Packaged Runtime provenance is owned by its local RUNTIME_COMMIT marker.
        # Never ask Git for HEAD unless the Runtime root itself owns a .git directory:
        # otherwise git -C walks to a parent product worktree and reports the wrong
        # Storage assembly commit as the Runtime provenance.
        marker = root / "RUNTIME_COMMIT"
        head = None
        if marker.is_file():
            head = marker.read_text(encoding="utf-8").strip()
        elif (root / ".git").exists():
            try:
                head = subprocess.check_output(
                    ["git", "-C", str(root), "rev-parse", "HEAD"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
            except Exception:
                head = None
        if not head:
            errors.append("无法校验 Runtime 版本：Runtime 根目录缺少 RUNTIME_COMMIT / 自有 .git")
        elif head != EXPECTED:
            errors.append(f"Runtime 基线不匹配: expected={EXPECTED}, actual={head}")
        else:
            notes.append("Runtime baseline PASS")
        for rel in [
            "config/runtime/model.yaml",
            "tools/openai_mock/server.py",
            "runtime/providers/openai_compatible.py",
        ]:
            if not (root / rel).exists():
                errors.append(f"Runtime 缺少 {rel}")

    project = Path(__file__).resolve().parents[1]
    for rel in [
        "storage_life/app.py",
        "storage_life/runtime_bridge.py",
        "config/runtime/storage.ai.json_call.yaml",
        "config/runtime/storage.emmc.parameter_extract.yaml",
        "prompts/runtime/storage/emmc_parameter_extract.md",
        "examples/synthetic_emmc.pdf",
    ]:
        if not (project / rel).exists():
            errors.append(f"Storage 包缺少 {rel}")

    if args.mode == "real" and root is not None:
        explicit = os.environ.get("STORAGE_MODEL_CONFIG", "").strip()
        if explicit:
            model_config = Path(explicit).expanduser()
            if not model_config.is_absolute():
                model_config = (project / model_config).resolve()
            else:
                model_config = model_config.resolve()
        else:
            model_config = root / "config" / "runtime" / "model.yaml"

        if not model_config.is_file():
            errors.append(f"Runtime Model Config 不存在: {model_config}")
        else:
            try:
                profile, base, key, source = _resolve_real_profile(model_config)
            except Exception as exc:
                errors.append(f"Runtime Model Config 无效: {type(exc).__name__}: {exc}")
            else:
                model = str(profile.get("model") or "").strip()
                parsed = urlsplit(base)
                final = base.rstrip("/")
                if final and not final.endswith("/chat/completions"):
                    final += "/chat/completions"

                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    errors.append("Real Provider base_url 必须是完整 http:// 或 https:// URL")
                if parsed.hostname and parsed.hostname.endswith(".maas.aliyuncs.com"):
                    if parsed.scheme != "https":
                        errors.append("DashScope Workspace 必须使用 HTTPS")
                    if not parsed.path.rstrip("/").endswith("/compatible-mode/v1"):
                        errors.append(
                            "DashScope Workspace base_url 必须包含 /compatible-mode/v1"
                        )
                elif parsed.scheme == "http":
                    notes.append("Internal/OpenAI-compatible HTTP provider allowed")
                if not model:
                    errors.append(f"models.{MODEL_REF}.model 为空")
                if not key:
                    notes.append("Provider API Key not configured; Runtime will use authless mode")

                print("PROVIDER_DIAGNOSTIC")
                print("  model_config=", model_config)
                print("  model_ref=", MODEL_REF)
                print("  config_source=", source)
                print("  scheme=", parsed.scheme or "<missing>")
                print("  host=", parsed.hostname or "<missing>")
                print("  base_path=", parsed.path or "/")
                print("  final_url=", final or "<missing>")
                print("  model=", model or "<missing>")
                print("  api_key_present=", bool(key))
                from urllib.request import getproxies, proxy_bypass
                proxies = getproxies()
                print("  proxy_bypass=", proxy_bypass(parsed.hostname) if parsed.hostname else False)
                for proxy_key in ("http", "https", "all"):
                    proxy_value = proxies.get(proxy_key)
                    if proxy_value:
                        proxy_parsed = urlsplit(proxy_value)
                        safe_proxy = (
                            f"{proxy_parsed.scheme}://{proxy_parsed.hostname}"
                            + (f":{proxy_parsed.port}" if proxy_parsed.port else "")
                            if proxy_parsed.scheme and proxy_parsed.hostname
                            else "<set>"
                        )
                        print(f"  proxy_{proxy_key}=", safe_proxy)

    print("PREFLIGHT")
    for item in notes:
        print("PASS", item)
    for item in errors:
        print("FAIL", item)
    if errors:
        raise SystemExit(2)
    print("PASS all checks")


if __name__ == "__main__":
    main()
