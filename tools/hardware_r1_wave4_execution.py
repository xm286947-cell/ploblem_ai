"""Wave4 real execution runner over the frozen R1 Workbench and asset stack."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import yaml

from services.hardware_asset_operation_journal import HardwareAssetOperationJournal
from services.hardware_asset_repository import CandidateAssetRepository
from services.hardware_case_r1_workbench import HardwareR1WorkbenchService, HardwareR1WorkbenchStore
from services.hardware_case_source_store import HardwareCaseSourceStore
from services.hardware_case_r1_preview_store import HardwareR1PreviewStore
from services.hardware_data_root import HardwareDataRootResolver
from services.hardware_startup_coordinator import HardwareStartupCoordinator


PRODUCT_COMMIT = "ca310a5062c8fa15e6a00d22c718fdc7504192c8"
PREPARE_SHA256 = "d8305b991ffc40b43c72d8aefde64db13c0128784efc75a5286cefc7c80621e0"
DATASET_CONTRACT = "hardware-r1-wave4-dataset/v1"


class Wave4ExecutionError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _path(config: dict[str, Any], key: str) -> Path:
    raw = str(config.get(key) or "").strip()
    if not raw:
        raise Wave4ExecutionError("CONFIG_FIELD_REQUIRED")
    return Path(raw).expanduser().resolve()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _load_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Wave4ExecutionError(code) from error
    if not isinstance(value, dict):
        raise Wave4ExecutionError(code)
    return value


def _validate_model_config(path: Path, application_root: Path) -> None:
    if _inside(path, application_root) or not path.is_file():
        raise Wave4ExecutionError("MODEL_CONFIG_LOCATION_INVALID")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise Wave4ExecutionError("MODEL_CONFIG_INVALID") from error
    if not isinstance(data, dict):
        raise Wave4ExecutionError("MODEL_CONFIG_INVALID")
    models = data.get("models")
    active = str(data.get("active_model") or "")
    profile = models.get(active) if isinstance(models, dict) else None
    if not isinstance(profile, dict):
        raise Wave4ExecutionError("MODEL_CONFIG_INVALID")
    inline = profile.get("api_key")
    if inline is not None and str(inline).strip() not in {
        "", "__REPLACE_LOCALLY_FOR_TEST_ONLY__", "__REPLACE_IN_YOUR_LOCAL_COPY_ONLY__"
    }:
        raise Wave4ExecutionError("INLINE_PROVIDER_SECRET_FORBIDDEN")
    for key, value in profile.items():
        if re.search(r"(api[_-]?key|secret|token)$", str(key), re.I) and key not in {"api_key_env", "base_url_env"}:
            if value not in (None, "", "__REPLACE_LOCALLY_FOR_TEST_ONLY__", "__REPLACE_IN_YOUR_LOCAL_COPY_ONLY__"):
                raise Wave4ExecutionError("INLINE_PROVIDER_SECRET_FORBIDDEN")
    for key in ("api_key_env", "base_url_env"):
        env_name = profile.get(key)
        if env_name and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(env_name)):
            raise Wave4ExecutionError("MODEL_CONFIG_INVALID")
        if key == "api_key_env" and env_name and not str(os.environ.get(str(env_name)) or "").strip():
            raise Wave4ExecutionError("PROVIDER_SECRET_ENV_REQUIRED")


def _dataset(manifest_path: Path, source_dir: Path, *, real_mode: bool) -> tuple[dict[str, Any], str, list[tuple[dict[str, Any], Path]]]:
    manifest = _load_json(manifest_path, "DATASET_MANIFEST_INVALID")
    if manifest.get("manifest_version") != DATASET_CONTRACT:
        raise Wave4ExecutionError("DATASET_MANIFEST_INVALID")
    records = manifest.get("records")
    if not isinstance(records, list) or int(manifest.get("case_count", -1)) != len(records):
        raise Wave4ExecutionError("DATASET_MANIFEST_INVALID")
    if real_mode and not 20 <= len(records) <= 30:
        raise Wave4ExecutionError("DATASET_COUNT_INVALID")
    if not real_mode and not records:
        raise Wave4ExecutionError("DATASET_COUNT_INVALID")
    ids = [str(record.get("source_id") or "").lower() for record in records if isinstance(record, dict)]
    if len(ids) != len(records) or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in ids):
        raise Wave4ExecutionError("DATASET_MANIFEST_INVALID")
    if len(set(ids)) != len(ids):
        raise Wave4ExecutionError("DUPLICATE_SOURCE_ID")
    bound: list[tuple[dict[str, Any], Path]] = []
    for record in records:
        relative = str(record.get("relative_name") or "")
        rel = Path(relative)
        if not relative or rel.is_absolute() or ".." in rel.parts:
            raise Wave4ExecutionError("DATASET_SOURCE_MISMATCH")
        source = (source_dir / rel).resolve()
        if not _inside(source, source_dir) or not source.is_file():
            raise Wave4ExecutionError("DATASET_SOURCE_MISMATCH")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        try:
            expected_size = int(record.get("file_size"))
        except (TypeError, ValueError) as error:
            raise Wave4ExecutionError("DATASET_SOURCE_MISMATCH") from error
        if digest != str(record.get("source_id")).lower() or source.stat().st_size != expected_size:
            raise Wave4ExecutionError("DATASET_SOURCE_MISMATCH")
        bound.append((record, source))
    return manifest, _canonical_hash(manifest), bound


def _startup(config: dict[str, Any], app_root: Path, *, allow_existing: bool = False) -> tuple[Path, dict[str, Any]]:
    data_root = _path(config, "validation_data_root")
    bootstrap = _path(config, "validation_bootstrap_path")
    normal_root = _path(config, "normal_product_data_root")
    if data_root == normal_root or _inside(data_root, normal_root) or _inside(normal_root, data_root):
        raise Wave4ExecutionError("VALIDATION_DATA_ROOT_COLLISION")
    if _inside(data_root, app_root) or _inside(app_root, data_root):
        raise Wave4ExecutionError("VALIDATION_DATA_ROOT_OVERLAPS_APPLICATION")
    if _inside(bootstrap, app_root) or _inside(bootstrap, data_root):
        raise Wave4ExecutionError("VALIDATION_BOOTSTRAP_LOCATION_INVALID")
    resolver = HardwareDataRootResolver(
        app_root,
        environment={"HARDWARE_DATA_ROOT": str(data_root)},
        bootstrap_path=bootstrap,
        legacy_roots=(),
    )
    preflight = resolver.resolve()
    if preflight.classification == "BLOCKED":
        code = preflight.error_code or "VALIDATION_STARTUP_BLOCKED"
        if code in {"PERSISTENT_DATA_ROOT_OVERLAPS_APPLICATION_ROOT", "BOOTSTRAP_LOCATION_INVALID"}:
            raise Wave4ExecutionError("VALIDATION_DATA_ROOT_OVERLAPS_APPLICATION" if code.startswith("PERSISTENT") else "VALIDATION_BOOTSTRAP_LOCATION_INVALID")
        raise Wave4ExecutionError("VALIDATION_STARTUP_BLOCKED")
    if preflight.classification != "FIRST_INSTALL" and not (allow_existing and preflight.classification == "EXISTING_INSTALL"):
        raise Wave4ExecutionError("VALIDATION_DATA_ROOT_NOT_FRESH")
    status = HardwareStartupCoordinator(app_root, resolver=resolver).run()
    if status.get("status") != "READY" or status.get("data_root") != str(data_root):
        raise Wave4ExecutionError("VALIDATION_STARTUP_BLOCKED")
    return data_root, status


def _workbench(data_root: Path, app_root: Path, model_config: Path, factory: Callable[[], Any] | None) -> tuple[HardwareR1WorkbenchService, CandidateAssetRepository]:
    db = data_root / "db"
    asset_repo = CandidateAssetRepository(db / "hardware_asset.db")
    journal = HardwareAssetOperationJournal(db / "hardware_asset.db")
    source_store = HardwareCaseSourceStore(
        db / "hardware_case_mvp.db", data_root / "sources",
        initialize_schema=False, operation_journal=journal, candidate_repository=asset_repo,
    )
    workbench_store = HardwareR1WorkbenchStore(data_root / "db" / "workbench_runtime.db")
    preview = HardwareR1PreviewStore(data_root / "rebuildable" / "hardware_r1_preview.db")
    if factory is None:
        os.environ["HARDWARE_DATA_ROOT"] = str(data_root)
        os.environ["HARDWARE_CASE_RUNTIME_DB"] = str(data_root / "audit/runtime/hardware_case_runtime.db")
        os.environ["HARDWARE_CASE_MODEL_CONFIG"] = str(model_config)
        factory = lambda: __import__("services.hardware_case_r1_runtime", fromlist=["build_hardware_case_r1_structurer"]).build_hardware_case_r1_structurer()
    service = HardwareR1WorkbenchService(
        workbench_store, source_store=source_store, structurer_factory=factory,
        preview_store=preview, candidate_repository=asset_repo,
    )
    return service, asset_repo


def execute(config_path: Path, *, execute_real: bool, structurer_factory: Callable[[], Any] | None = None, retry_failed: bool = False) -> dict[str, Any]:
    config_path = config_path.resolve()
    config = _load_json(config_path, "CONFIG_INVALID")
    if int(config.get("max_cases", 0)) != 30:
        raise Wave4ExecutionError("MAX_CASES_INVALID")
    allow_real = config.get("allow_real_provider") is True
    if execute_real != allow_real:
        raise Wave4ExecutionError("REAL_PROVIDER_EXECUTION_NOT_AUTHORIZED")
    if not execute_real and structurer_factory is None:
        raise Wave4ExecutionError("REAL_PROVIDER_EXECUTION_NOT_AUTHORIZED")

    app_root = Path(__file__).resolve().parents[1]
    source_dir = _path(config, "source_dir")
    manifest_path = _path(config, "dataset_manifest")
    output_dir = _path(config, "output_dir")
    model_config = _path(config, "model_config")
    if execute_real:
        _validate_model_config(model_config, app_root)
    manifest, manifest_hash, bound = _dataset(manifest_path, source_dir, real_mode=execute_real)
    data_root = _path(config, "validation_data_root")
    if _inside(output_dir, app_root) or _inside(output_dir, data_root):
        raise Wave4ExecutionError("OUTPUT_LOCATION_INVALID")
    data_root, startup = _startup(config, app_root, allow_existing=retry_failed)

    first_report = output_dir / "WAVE4_EXECUTION_EVIDENCE.json"
    summary_path = output_dir / "WAVE4_EXECUTION_SUMMARY.json"
    if retry_failed:
        if not first_report.is_file() or not summary_path.is_file():
            raise Wave4ExecutionError("FIRST_PASS_REPORT_REQUIRED")
        prior = _load_json(first_report, "FIRST_PASS_REPORT_INVALID")
        if prior.get("dataset_manifest_sha256") != manifest_hash:
            raise Wave4ExecutionError("RETRY_DATASET_HASH_MISMATCH")
        if prior.get("installation_id") != _existing_installation_id(_path(config, "validation_bootstrap_path")):
            raise Wave4ExecutionError("RETRY_DATA_ROOT_MISMATCH")
        service, repository = _workbench(data_root, app_root, model_config, structurer_factory)
        batch_id = str(prior.get("batch_id") or "")
        if not batch_id:
            raise Wave4ExecutionError("FIRST_PASS_REPORT_INVALID")
        retry = service.retry_failed_only(batch_id)
        retry_path = output_dir / "WAVE4_RETRY_REPORT.json"
        retry_evidence = {
            "contract_version": "hardware-r1-wave4-retry-report/v1",
            "dataset_manifest_sha256": manifest_hash,
            "batch_id": batch_id,
            "retry_selected_count": int(retry.get("retry_selected_count") or 0),
            "result_counts": retry.get("summary"),
            "first_pass_report_immutable": True,
        }
        _write_exclusive(retry_path, retry_evidence)
        return {"batch": retry, "summary": retry_evidence, "evidence": prior, "repository": repository}
    if first_report.exists() or summary_path.exists():
        raise Wave4ExecutionError("FIRST_PASS_REPORT_ALREADY_EXISTS")
    service, repository = _workbench(data_root, app_root, model_config, structurer_factory)
    files = [
        (f"{record.get('business_case_id') or f'W4-{index:03d}'}-source.docx", path.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        for index, (record, path) in enumerate(bound, 1)
    ]
    uploaded = service.upload_batch(files)
    batch_id = str(uploaded.get("batch_id") or "")
    before_runtime = _provider_calls_in_root(data_root)
    started = time.monotonic()
    batch = service.run_batch(batch_id)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    items = batch.get("items") or []
    evidence_records = []
    statuses = []
    for index, item in enumerate(items):
        record = bound[index][0]
        candidate_asset = item.get("candidate_asset") if isinstance(item.get("candidate_asset"), dict) else {}
        pipeline = item.get("pipeline_result") if isinstance(item.get("pipeline_result"), dict) else {}
        runtime = pipeline.get("runtime") if isinstance(pipeline.get("runtime"), dict) else {}
        calls = int(pipeline.get("provider_call_count") or 0)
        statuses.append(str(item.get("result") or "FAILED"))
        evidence_records.append({
            "case_alias": f"W4-{index + 1:03d}",
            "source_id": record["source_id"],
            "business_case_id": item.get("business_case_id"),
            "run_ref": pipeline.get("run_id"),
            "candidate_id": item.get("candidate_id"),
            "parse": item.get("parse"), "stage_a": item.get("stage_a"), "stage_b": item.get("stage_b"),
            "evidence_gate": (pipeline.get("evidence_validation") or {}).get("status"),
            "candidate_commit": "PASS" if item.get("candidate_id") else "NOT_COMMITTED",
            "result": item.get("result"), "failed_stage": item.get("failed_stage"),
            "error_code": item.get("error_code"), "provider_calls": calls,
            "stage_a_provider_attempts": _stage_calls(runtime, "stage_a"),
            "stage_b_provider_attempts": _stage_calls(runtime, "stage_b"),
            "duration_ms": pipeline.get("duration_ms"),
            "candidate_source_identity_check": bool(candidate_asset and candidate_asset.get("source_id") == record["source_id"]),
            "production_review_status": candidate_asset.get("production_review_status"),
        })
    after_runtime = _provider_calls_in_root(data_root)
    # Test injection runs only in non-real mode. No external provider object is built.
    evidence = {
        "contract_version": "hardware-r1-wave4-execution-evidence/v1",
        "source_product_commit": PRODUCT_COMMIT,
        "execution_package_id": "HARDWARE_R1_WAVE4_REAL_EXECUTION_ca310a5062c8",
        "dataset_manifest_sha256": manifest_hash,
        "installation_id": startup.get("installation_id"),
        "batch_id": batch_id,
        "run_ref": "W4RUN-" + uuid4().hex,
        "provider_calls": max(after_runtime - before_runtime, sum(int(x.get("provider_calls") or 0) for x in evidence_records)),
        "records": evidence_records,
        "elapsed_ms": elapsed_ms,
    }
    summary = {
        "contract_version": "hardware-r1-wave4-execution-summary/v1",
        "source_product_commit": PRODUCT_COMMIT,
        "execution_tool_commit": os.getenv("WAVE4_EXECUTION_TOOL_COMMIT", "UNBOUND"),
        "dataset_manifest_sha256": manifest_hash,
        "total_count": len(items),
        "result_counts": {key: statuses.count(key) for key in sorted(set(statuses))},
        "provider_call_total": evidence["provider_calls"],
        "elapsed_ms_total": elapsed_ms,
        "review_required_count": sum(str(x.get("production_review_status") or "").upper() == "REQUIRED" for x in evidence_records),
        "auto_review": False, "auto_publish": False,
        "image_evidence_policy": "IMAGE_DEPENDENT_DEFERRED",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_exclusive(first_report, evidence)
    _write_exclusive(summary_path, summary)
    return {"batch": batch, "summary": summary, "evidence": evidence, "repository": repository}


def _stage_calls(runtime: dict[str, Any], stage: str) -> int | None:
    value = runtime.get(stage)
    if not isinstance(value, dict):
        return None
    for key in ("provider_call_count", "provider_calls", "attempt_count"):
        if value.get(key) is not None:
            try:
                return int(value[key])
            except (TypeError, ValueError):
                return None
    return None


def _provider_calls_in_root(data_root: Path) -> int:
    # Runtime adapters report calls in their result; database deltas are intentionally not guessed.
    return 0


def _existing_installation_id(bootstrap_path: Path) -> str | None:
    try:
        return str(json.loads(bootstrap_path.read_text(encoding="utf-8")).get("installation_id") or "") or None
    except (OSError, json.JSONDecodeError):
        return None


def _write_exclusive(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except FileExistsError as error:
        raise Wave4ExecutionError("FIRST_PASS_REPORT_ALREADY_EXISTS") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute the frozen Wave4 dataset through the R1 Workbench")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--execute-real", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args(argv)
    try:
        execute(args.config, execute_real=args.execute_real, retry_failed=args.retry_failed)
    except Wave4ExecutionError as error:
        print(error.code, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
