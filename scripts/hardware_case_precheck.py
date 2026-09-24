from __future__ import annotations

import argparse
import importlib
import os
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MODEL_EXAMPLE = ROOT / "config/runtime/model.local.hardware_case.example.yaml"
MODEL_LOCAL = ROOT / "config/runtime/model.local.yaml"
VALIDATION_EXAMPLE = ROOT / "config/hardware_case_real_validation.local.example.json"
VALIDATION_LOCAL = ROOT / "config/hardware_case_real_validation.local.json"

REQUIRED_MODULES = (
    "fastapi",
    "uvicorn",
    "openpyxl",
    "jsonschema",
    "yaml",
)


def emit(name: str, status: str, detail: str = "") -> None:
    suffix = f" | {detail}" if detail else ""
    print(f"[{status}] {name}{suffix}")


def ensure_local_templates() -> None:
    MODEL_LOCAL.parent.mkdir(parents=True, exist_ok=True)
    if not MODEL_LOCAL.exists():
        shutil.copy2(MODEL_EXAMPLE, MODEL_LOCAL)
        emit("model.local.yaml", "CREATED", str(MODEL_LOCAL.relative_to(ROOT)))
    if not VALIDATION_LOCAL.exists():
        shutil.copy2(VALIDATION_EXAMPLE, VALIDATION_LOCAL)
        emit(
            "hardware_case_real_validation.local.json",
            "CREATED",
            str(VALIDATION_LOCAL.relative_to(ROOT)),
        )
    for relative in (
        "data/input/word",
        "data/tree",
        "data/output",
        "data/runtime",
    ):
        (ROOT / relative).mkdir(parents=True, exist_ok=True)


def check_python() -> list[str]:
    errors: list[str] = []
    if sys.version_info < (3, 11):
        errors.append("PYTHON_3_11_REQUIRED")
        emit("Python", "FAIL", sys.version.split()[0])
    else:
        emit("Python", "PASS", sys.version.split()[0])
    for module in REQUIRED_MODULES:
        try:
            importlib.import_module(module)
            emit(f"module:{module}", "PASS")
        except Exception:
            errors.append(f"PYTHON_MODULE_MISSING:{module}")
            emit(f"module:{module}", "FAIL")
    return errors


def check_web() -> list[str]:
    errors: list[str] = []
    required = (
        "main.py",
        "quality_knowledge/web/templates/hardware_tree_import.html",
        "quality_knowledge/web/static/hardware_tree_import.js",
        "quality_knowledge/web/static/hardware_tree_import.css",
    )
    for relative in required:
        path = ROOT / relative
        if path.is_file():
            emit(relative, "PASS")
        else:
            errors.append(f"PACKAGE_FILE_MISSING:{relative}")
            emit(relative, "FAIL")
    for relative in ("data", "data/runtime"):
        path = ROOT / relative
        path.mkdir(parents=True, exist_ok=True)
        try:
            probe = path / ".precheck-write"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            emit(f"writable:{relative}", "PASS")
        except OSError:
            errors.append(f"DIRECTORY_NOT_WRITABLE:{relative}")
            emit(f"writable:{relative}", "FAIL")
    return errors


def _contains_placeholder(value: str) -> bool:
    text = str(value or "").upper()
    return any(token in text for token in ("REPLACE_WITH", "__REPLACE", "YOUR_"))


def check_real_ai() -> list[str]:
    errors: list[str] = []
    if not MODEL_LOCAL.is_file():
        errors.append("MODEL_LOCAL_CONFIG_REQUIRED")
        emit(
            "config/runtime/model.local.yaml",
            "FAIL",
            "run INIT_LOCAL_CONFIG.bat first",
        )
        return errors

    try:
        raw = yaml.safe_load(MODEL_LOCAL.read_text(encoding="utf-8"))
    except Exception:
        errors.append("MODEL_LOCAL_CONFIG_INVALID")
        emit("model.local.yaml", "FAIL", "invalid YAML")
        return errors

    if not isinstance(raw, dict):
        errors.append("MODEL_LOCAL_CONFIG_INVALID")
        emit("model.local.yaml", "FAIL", "root must be mapping")
        return errors

    active = str(raw.get("active_model") or "").strip()
    models = raw.get("models")
    if not active or not isinstance(models, dict) or active not in models:
        errors.append("ACTIVE_MODEL_INVALID")
        emit("active_model", "FAIL", active or "<empty>")
        return errors
    profile = models[active]
    if not isinstance(profile, dict):
        errors.append("ACTIVE_MODEL_PROFILE_INVALID")
        emit("active_model profile", "FAIL")
        return errors

    model = str(profile.get("model") or "").strip()
    base_url = str(profile.get("base_url") or "").strip()
    base_url_env = str(profile.get("base_url_env") or "").strip()
    api_key_env = str(profile.get("api_key_env") or "").strip()

    if not model or _contains_placeholder(model):
        errors.append("MODEL_NAME_NOT_CONFIGURED")
        emit("model", "FAIL", model or "<empty>")
    else:
        emit("model", "PASS", model)

    resolved_base = os.environ.get(base_url_env, "").strip() if base_url_env else base_url
    if not resolved_base or _contains_placeholder(resolved_base):
        errors.append("BASE_URL_NOT_CONFIGURED")
        emit("base_url", "FAIL", base_url_env or resolved_base or "<empty>")
    else:
        emit("base_url", "PASS", resolved_base)

    if api_key_env:
        if not os.environ.get(api_key_env, "").strip():
            errors.append(f"SECRET_ENV_NOT_FOUND:{api_key_env}")
            emit("api_key_env", "FAIL", api_key_env)
        else:
            emit("api_key_env", "PASS", api_key_env)
    elif profile.get("api_key"):
        emit(
            "api_key",
            "WARN",
            "local literal key is supported by Runtime but env reference is recommended",
        )
    else:
        emit("provider_auth", "PASS", "none")

    if errors:
        return errors

    try:
        from services.hardware_case_runtime_adapter import (
            HARDWARE_CASE_STRUCTURE_SCHEMA,
        )
        from runtime.config import AgentConfigLoader

        loader = AgentConfigLoader(
            root=ROOT,
            model_profiles=MODEL_LOCAL,
            schemas={"HardwareCaseStructureOutput": HARDWARE_CASE_STRUCTURE_SCHEMA},
            environ=os.environ,
        )
        resolved = loader.load(
            ROOT / "config/runtime/agents/hardware_case.structure.yaml"
        )
        emit("agent_id", "PASS", resolved.definition.agent_id)
        emit("runtime_provider", "PASS", resolved.provider.type)
        emit("runtime_model", "PASS", resolved.provider.model)
        emit("agent_config_hash", "PASS", resolved.config_hash[:16])
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        errors.append(f"RUNTIME_CONFIG_INVALID:{code}")
        emit("Unified Runtime config", "FAIL", str(code))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("web", "real-ai", "all"),
        default="all",
    )
    parser.add_argument("--init-local", action="store_true")
    args = parser.parse_args()

    if args.init_local:
        ensure_local_templates()

    errors = check_python()
    if args.mode in {"web", "all"}:
        errors.extend(check_web())
    if args.mode in {"real-ai", "all"}:
        errors.extend(check_real_ai())

    if errors:
        print("")
        print("RESULT=BLOCKED")
        for error in errors:
            print(f"BLOCKER={error}")
        return 2

    print("")
    print("RESULT=PASS")
    print(f"MODE={args.mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
