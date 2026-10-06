from __future__ import annotations

import gc
import importlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import yaml
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
os.environ.pop("PYTHONPATH", None)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality_knowledge.web.p0_app import create_p0_app


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode != 0:
        raise SystemExit(
            "FRESH_EXTRACT_COMMAND_FAIL="
            + " ".join(command)
            + "|"
            + output[-1500:].replace("\n", " ")
        )
    return output


def _write_runtime_config(path: Path) -> None:
    path.write_text(
        """active_model: hardware_fresh_extract
models:
  hardware_fresh_extract:
    provider: openai_compatible
    base_url: http://127.0.0.1:9/v1
    model: REPLACE_WITH_COMPANY_APPROVED_MODEL
    metadata:
      configuration_state: CONFIGURATION_REQUIRED
      startup_profile: NON_SECRET_LOOPBACK_BOOTSTRAP
""",
        encoding="utf-8",
    )


def _startup_check(work: Path) -> None:
    command = [
        sys.executable,
        str(ROOT / "scripts/hardware_case_web_start.py"),
        "--check",
        "--db", str(work / "quality.db"),
        "--hardware-db", str(work / "hardware.db"),
        "--tree-upload-dir", str(work / "tree"),
        "--source-root", str(work / "sources"),
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
    )
    if result.returncode != 0 or "RESULT=PASS" not in result.stdout:
        raise SystemExit(
            "FRESH_EXTRACT_STARTUP_CHECK_FAIL="
            + (result.stdout + result.stderr)[-1200:].replace("\n", " ")
        )


def _verify_asset_migration_imports(manifest: dict) -> None:
    package = manifest.get("asset_migration_package")
    if not isinstance(package, dict) or package.get("status") != "PASS":
        raise SystemExit("FRESH_EXTRACT_ASSET_MIGRATION_MANIFEST_INVALID")
    modules = package.get("modules")
    if not isinstance(modules, list):
        raise SystemExit("FRESH_EXTRACT_ASSET_MIGRATION_LIST_MISSING")
    required = {
        "services/hardware_asset_migrations/v001_candidate_repository.py",
        "services/hardware_asset_migrations/v002_legacy_migration.py",
        "services/hardware_asset_migrations/v003_source_operation_journal.py",
    }
    if not required.issubset(set(modules)):
        raise SystemExit("FRESH_EXTRACT_ASSET_MIGRATION_MODULES_MISSING")
    packaged_paths = {item.get("path") for item in manifest.get("files", [])}
    if not required.issubset(packaged_paths):
        raise SystemExit("FRESH_EXTRACT_ASSET_MIGRATION_FILES_MISSING")
    try:
        importlib.import_module("services.hardware_asset_repository")
        for relative in modules:
            module = Path(str(relative)).with_suffix("").as_posix().replace("/", ".")
            if module.endswith(".__init__"):
                module = module[: -len(".__init__")]
            importlib.import_module(module)
        importlib.import_module("repositories.json_repository")
    except (ImportError, ValueError) as error:
        raise SystemExit("FRESH_EXTRACT_ASSET_MIGRATION_IMPORT_FAIL=" + str(error)) from error


def _create_app(work: Path):
    return create_p0_app(
        work / "quality-runtime.db",
        project_root=ROOT,
        hardware_case_db_path=work / "hardware-ready.db",
        hardware_tree_upload_dir=work / "tree-ready",
        hardware_case_source_root=work / "sources-ready",
        hardware_r1_workbench_db_path=work / "workbench.db",
        hardware_r1_preview_db_path=work / "preview.db",
        enabled_domains={"HARDWARE_CASE"},
    )


def _set_smoke_environment(data_root: Path) -> dict[str, str | None]:
    names = (
        "PYTHONPATH", "HARDWARE_DATA_ROOT", "HARDWARE_R1_E2E_PROFILE",
        "HARDWARE_R1_E2E_KNOWLEDGE_ENV", "HARDWARE_R1_E2E_KNOWLEDGE_MODE",
        "HARDWARE_KNOWLEDGE_RELEASE_VERSION", "HARDWARE_CASE_MODEL_CONFIG",
        "HARDWARE_CASE_AGENT_CONFIG", "HARDWARE_CASE_RUNTIME_DB",
        "HARDWARE_CASE_API_KEY", "HARDWARE_KNOWLEDGE_BASE_URL",
        "HARDWARE_KNOWLEDGE_READINESS_URL",
    )
    previous = {name: os.environ.get(name) for name in names}
    os.environ.pop("PYTHONPATH", None)
    os.environ.pop("HARDWARE_CASE_MODEL_CONFIG", None)
    os.environ.pop("HARDWARE_CASE_AGENT_CONFIG", None)
    os.environ.pop("HARDWARE_CASE_RUNTIME_DB", None)
    os.environ.pop("HARDWARE_CASE_API_KEY", None)
    os.environ.pop("HARDWARE_KNOWLEDGE_BASE_URL", None)
    os.environ.pop("HARDWARE_KNOWLEDGE_READINESS_URL", None)
    os.environ["HARDWARE_DATA_ROOT"] = str(data_root)
    os.environ["HARDWARE_R1_E2E_PROFILE"] = "1"
    os.environ["HARDWARE_R1_E2E_KNOWLEDGE_ENV"] = "NON_PROD"
    os.environ["HARDWARE_R1_E2E_KNOWLEDGE_MODE"] = "LOCAL_NON_PROD"
    os.environ["HARDWARE_KNOWLEDGE_RELEASE_VERSION"] = "CI_FRESH_EXTRACT"
    return previous


def _restore_environment(previous: dict[str, str | None]) -> None:
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _verify_missing_config_is_explicit(work: Path) -> None:
    app = _create_app(work / "missing-config")
    with TestClient(app) as client:
        health = client.get("/health")
        if health.status_code != 200:
            raise SystemExit("MISSING_CONFIG_HEALTH_FAIL")
        ready = client.get("/ready")
        if ready.status_code != 503:
            raise SystemExit("MISSING_CONFIG_DID_NOT_FAIL_CLOSED")
        runtime = ready.json()["dependencies"]["UNIFIED_RUNTIME_CONFIG"]
        if runtime.get("status") != "UNREADY" or runtime.get("error_code") != (
            "RUNTIME_CONFIG_INVALID:MODEL_LOCAL_CONFIG_REQUIRED"
        ):
            raise SystemExit("MISSING_CONFIG_NOT_EXPLICIT=" + json.dumps(runtime))


def _verify_configurable_ready(work: Path) -> None:
    app = _create_app(work / "configured-ready")
    with TestClient(app) as client:
        health = client.get("/health")
        if health.status_code != 200 or health.json().get("status") != "HEALTHY":
            raise SystemExit("FRESH_EXTRACT_HEALTH_FAIL")

        ready = client.get("/ready")
        if ready.status_code != 200 or ready.json().get("status") != "READY":
            raise SystemExit("FRESH_EXTRACT_READY_FAIL=" + ready.text[:1000])
        payload = ready.json()
        runtime = payload["dependencies"]["UNIFIED_RUNTIME_CONFIG"]
        if runtime.get("configuration_state") != "CONFIGURATION_REQUIRED":
            raise SystemExit("CONFIGURABLE_READY_STATE_MISSING")
        if runtime.get("provider_connectivity") != "NOT_PROBED":
            raise SystemExit("PROVIDER_READINESS_SEMANTICS_CHANGED")

        descriptor = client.get("/api/public/hardware/v1/contract")
        if descriptor.status_code != 200:
            raise SystemExit("PUBLIC_CONTRACT_DESCRIPTOR_FAIL")
        binding = descriptor.json()
        manifest = json.loads((ROOT / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
        if binding.get("public_contract_version") != "hardware-public-consumer/v1":
            raise SystemExit("PUBLIC_CONTRACT_VERSION_MISMATCH")
        for field in ("source_commit", "schema_version", "product_version"):
            manifest_key = "source_commit" if field == "source_commit" else field
            if binding.get(field) != manifest.get(manifest_key):
                raise SystemExit("PUBLIC_CONTRACT_BINDING_MISMATCH=" + field)
        if client.get("/api/public/hardware/v999/contract").status_code != 404:
            raise SystemExit("UNKNOWN_VERSION_NOT_FAIL_CLOSED")


def _verify_import_closure(manifest: dict) -> None:
    closure = json.loads((ROOT / "PACKAGE_DEPENDENCY_CLOSURE.json").read_text(encoding="utf-8"))
    if closure.get("status") != "PASS" or closure.get("unresolved_local_imports"):
        raise SystemExit("RECURSIVE_LOCAL_IMPORT_CLOSURE_FAIL")
    closure_paths = set(closure.get("files") or [])
    packaged_paths = {item.get("path") for item in manifest.get("files", [])}
    if "repositories/json_repository.py" not in closure_paths or "repositories/json_repository.py" not in packaged_paths:
        raise SystemExit("JSON_REPOSITORY_MISSING_FROM_CLEAN_EXTRACT")

    modules = []
    for relative in sorted(closure_paths):
        path = Path(relative)
        if path.suffix != ".py":
            continue
        module = path.with_suffix("").as_posix().replace("/", ".")
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        if module and module not in modules:
            modules.append(module)
    script = (
        "import importlib, pathlib, sys; "
        f"root=pathlib.Path({str(ROOT)!r}).resolve(); "
        "sys.path.insert(0, str(root)); "
        f"modules={modules!r}; "
        "loaded=[importlib.import_module(name) for name in modules]; "
        "assert all(root in pathlib.Path(m.__file__).resolve().parents or pathlib.Path(m.__file__).resolve()==root for m in loaded if getattr(m,'__file__',None)), 'IMPORT_OUTSIDE_EXTRACT'"
    )
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=tempfile.gettempdir(),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise SystemExit(
            "PACKAGE_IMPORT_FAIL="
            + (result.stdout + result.stderr)[-1800:].replace("\n", " ")
        )


def main() -> int:
    manifest_path = ROOT / "PACKAGE_MANIFEST.json"
    if not manifest_path.is_file():
        raise SystemExit("PACKAGE_MANIFEST_MISSING")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _verify_asset_migration_imports(manifest)
    _verify_import_closure(manifest)

    required_files = (
        "START_HARDWARE_CASE.bat",
        "START_HARDWARE_CASE.sh",
        "START_HARDWARE_CASE.command",
        "INIT_LOCAL_CONFIG.bat",
        "INIT_LOCAL_CONFIG.sh",
        "quality_knowledge/web/hardware_public_api.py",
        "quality_knowledge/web/hardware_operability_api.py",
        "services/hardware_operability.py",
        "schema/hardware_public_consumer_v1.schema.json",
        "scripts/hardware_case_complete_product_smoke.py",
    )
    for relative in required_files:
        if not (ROOT / relative).is_file():
            raise SystemExit("FRESH_EXTRACT_FILE_MISSING=" + relative)

    file_paths = {str(item.get("path")) for item in manifest.get("files", [])}
    for forbidden in (
        "config/runtime/model.local.yaml",
        "config/runtime/model.local.yml",
        ".env",
        ".env.local",
    ):
        if forbidden in file_paths:
            raise SystemExit("SENSITIVE_CONFIG_INCLUDED=" + forbidden)
    runtime = manifest.get("runtime") or {}
    if runtime.get("model_template") != "config/runtime/model.local.hardware_case.example.yaml":
        raise SystemExit("MODEL_CONFIG_EXAMPLE_NOT_BOUND")
    if manifest.get("config_lifecycle", {}).get("secret_in_package") is not False:
        raise SystemExit("SECRET_PACKAGE_POLICY_INVALID")

    with tempfile.TemporaryDirectory(prefix="hardware-fresh-extract-") as temp:
        work = Path(temp)
        previous = _set_smoke_environment(work / "data")
        try:
            _startup_check(work)
            _verify_missing_config_is_explicit(work)
            model_config = ROOT / "config/runtime/model.local.yaml"
            if model_config.exists():
                raise SystemExit("FRESH_EXTRACT_LOCAL_CONFIG_ALREADY_PRESENT")
            if os.name == "nt":
                _run(["cmd", "/c", "INIT_LOCAL_CONFIG.bat"], cwd=ROOT)
            else:
                _run(["sh", "INIT_LOCAL_CONFIG.sh"], cwd=ROOT)
            if not model_config.is_file():
                raise SystemExit("MODEL_CONFIG_BOOTSTRAP_FAIL")
            template = (ROOT / "config/runtime/model.local.hardware_case.example.yaml").read_bytes()
            if model_config.read_bytes() != template:
                raise SystemExit("MODEL_CONFIG_BOOTSTRAP_CONTENT_MISMATCH")
            parsed = yaml.safe_load(model_config.read_text(encoding="utf-8"))
            active = parsed["models"][parsed["active_model"]]
            if "api_key" in active or "api_key_env" in active:
                raise SystemExit("REAL_SECRET_INCLUDED_IN_BOOTSTRAP")

            _verify_configurable_ready(work)
            smoke_env = os.environ.copy()
            smoke_env.pop("PYTHONPATH", None)
            smoke_output = _run(
                [sys.executable, "scripts/hardware_case_complete_product_smoke.py"],
                cwd=ROOT,
                env=smoke_env,
            )
            for marker in (
                "WORD_IMPORT_ENTRY=PASS",
                "GOLDEN_PREVIEW_ENTRY=PASS",
                "HUMAN_REVIEW_ENTRY=PASS",
                "PUBLISH_ENTRY=PASS",
                "RECONCILIATION_ENTRY=PASS",
                "KNOWLEDGE_SEARCH_ENTRY=PASS",
                "KNOWLEDGE_DETAIL_ENTRY=PASS",
            ):
                if marker not in smoke_output:
                    raise SystemExit("PRODUCT_SMOKE_MARKER_MISSING=" + marker)
        finally:
            _restore_environment(previous)

        model_config.unlink(missing_ok=True)
        validation_local = ROOT / "config/hardware_case_real_validation.local.json"
        validation_local.unlink(missing_ok=True)

    print("FRESH_EXTRACT_STARTUP=PASS")
    print("PACKAGE_IMPORT=PASS")
    print("JSON_REPOSITORY_PRESENT=YES")
    print("RECURSIVE_LOCAL_IMPORT_CLOSURE=PASS")
    print("UNRESOLVED_LOCAL_IMPORTS=0")
    print("DEV_WORKSPACE_DEPENDENCY=NO")
    print("MANUAL_FILE_INJECTION=NO")
    print("MODEL_CONFIG_BOOTSTRAP_CONTRACT=NON_SECRET_LOOPBACK_TO_LOCAL_CONFIG")
    print("REAL_SECRET_INCLUDED=NO")
    print("MODEL_LOCAL_REAL_CONFIG_INCLUDED=NO")
    print("MISSING_CONFIG_FILE_CRASH=NO")
    print("READY_SEMANTICS=CONFIG_VALIDATED_PROVIDER_CONNECTIVITY_NOT_PROBED")
    print("HEALTH=PASS")
    print("READY=PASS_PER_FROZEN_CONTRACT")
    print("PRODUCT_SMOKE=PASS")
    print("SECRET_SAFETY=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
