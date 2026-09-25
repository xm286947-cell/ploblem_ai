from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def main() -> int:
    # Import the exact same Storage runtime bridge used by the web application.
    from storage_life import runtime_bridge
    import runtime as runtime_package
    from runtime.providers.endpoint import ProviderEndpointError, ProviderEndpointResolver
    from runtime.providers import openai_compatible

    status = runtime_bridge.status()
    if not status.get("configured"):
        raise SystemExit(
            "RUNTIME_CONFIG_NOT_CONFIGURED: " + str(status.get("error") or status)
        )

    base_url = str(status.get("base_url") or "")
    parsed = urlsplit(base_url)
    try:
        final_url = ProviderEndpointResolver.chat_completions_url(base_url)
        endpoint_error = None
    except ProviderEndpointError as exc:
        final_url = None
        endpoint_error = exc.details

    config_raw = (
        (status.get("runtime") or {}).get("model_config")
        or os.environ.get("STORAGE_MODEL_CONFIG")
        or ""
    )
    config_path = Path(str(config_raw)).expanduser().resolve() if config_raw else None
    adapter_path = Path(openai_compatible.__file__).resolve()

    runtime_info = status.get("runtime") or {}
    runtime_module_path = Path(
        str(runtime_info.get("runtime_module") or runtime_package.__file__)
    ).resolve()
    storage_bridge_path = Path(runtime_bridge.__file__).resolve()
    safe = {
        "package_root": str(ROOT.resolve()),
        "working_dir": str(Path.cwd().resolve()),
        "python_executable": str(Path(sys.executable).resolve()),
        "python_prefix": str(Path(sys.prefix).resolve()),
        "virtual_env": os.environ.get("VIRTUAL_ENV"),
        "execution_mode": status.get("execution_mode"),
        "runtime_root": status.get("runtime_root"),
        "runtime_expected_commit": status.get("runtime_expected_commit"),
        "runtime_snapshot_commit": runtime_info.get("snapshot_commit"),
        "runtime_head": runtime_info.get("head"),
        "runtime_pinned_to_storage_bridge": runtime_info.get("pinned"),
        "runtime_draft_override": os.environ.get("STORAGE_LIFE_ALLOW_UNPINNED_RUNTIME") == "1",
        "runtime_module": str(runtime_module_path),
        "runtime_module_sha256": sha256_file(runtime_module_path),
        "storage_runtime_bridge_module": str(storage_bridge_path),
        "storage_runtime_bridge_module_sha256": sha256_file(storage_bridge_path),
        "runtime_model_config": str(config_path) if config_path else None,
        "runtime_model_config_sha256": sha256_file(config_path) if config_path else None,
        "model_config_source": os.environ.get("STORAGE_MODEL_CONFIG_SOURCE"),
        "agent_configs": runtime_info.get("agent_configs") or {},
        "runtime_provider_adapter": str(adapter_path),
        "runtime_provider_adapter_sha256": sha256_file(adapter_path),
        "agent_id": "storage.emmc.parameter_extract",
        "model_ref": status.get("profile"),
        "provider": status.get("provider"),
        "model": status.get("model"),
        "base_url": base_url,
        "scheme": parsed.scheme,
        "host": parsed.hostname,
        "port": parsed.port
        or (
            443
            if parsed.scheme == "https"
            else 80 if parsed.scheme == "http" else None
        ),
        "path": parsed.path,
        "final_url": final_url,
        "endpoint_contract_error": endpoint_error,
        "base_url_env": status.get("base_url_env"),
        "api_key_env": status.get("api_key_env"),
        "api_key_present": bool(status.get("api_key_present")),
        "storage_model_config_env": os.environ.get("STORAGE_MODEL_CONFIG"),
        "runtime_provider_trace": os.environ.get("RUNTIME_PROVIDER_TRACE"),
        "runtime_provider_trace_file": os.environ.get("RUNTIME_PROVIDER_TRACE_FILE"),
        "http_proxy_present": bool(
            os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
        ),
        "https_proxy_present": bool(
            os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        ),
        "no_proxy": os.environ.get("NO_PROXY") or os.environ.get("no_proxy"),
    }
    out = ROOT / "logs" / "effective_runtime_config.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")

    print("EFFECTIVE_RUNTIME_CONFIG")
    for key in (
        "package_root",
        "working_dir",
        "python_executable",
        "virtual_env",
        "execution_mode",
        "runtime_root",
        "runtime_expected_commit",
        "runtime_snapshot_commit",
        "runtime_pinned_to_storage_bridge",
        "runtime_draft_override",
        "runtime_module",
        "storage_runtime_bridge_module",
        "runtime_model_config",
        "runtime_model_config_sha256",
        "model_config_source",
        "agent_configs",
        "runtime_provider_adapter",
        "agent_id",
        "model_ref",
        "provider",
        "model",
        "base_url",
        "final_url",
        "api_key_present",
        "runtime_provider_trace_file",
        "http_proxy_present",
        "https_proxy_present",
        "no_proxy",
    ):
        print(f"  {key}={safe.get(key)}")
    print(f"  evidence={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
