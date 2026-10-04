"""Storage -> Unified Agent Runtime upper-layer adapter.

Runtime is a public platform dependency. Product code must not fork it; field test
packages may bundle a pinned Runtime snapshot for reproducible acceptance. This module owns only Storage-side adaptation:

- locate and pin the public Runtime checkout;
- load Storage-owned Runtime Agent configs;
- project Storage prompt/payload/schema into AgentRequest;
- map Runtime result/error back to Storage's AI adapter contract;
- expose execution telemetry for product testing.

The Provider call is executed by the public Runtime provider adapter.
Storage owns only the business envelope, schema, prompt semantics and result mapping;
Retry/Task/Resume/Budget/Provider HTTP remain owned by Unified Agent Runtime.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any
from uuid import uuid4

RUNTIME_EXPECTED_COMMIT = "9e36eeb0237459b884ee0d3e663ccf5833bfc685"
GENERIC_AGENT_ID = "storage.ai.json_call"
EMMC_PARAMETER_AGENT_ID = "storage.emmc.parameter_extract"
KNOWLEDGE_PRODUCTION_AGENT_ID = "knowledge.production.extract"
FOUR_FAMILY_DEVICE_TYPES = ("NOR Flash", "NAND Flash", "eMMC", "SSD")

_LOCK = threading.RLock()
_RUNTIME = None
_EMMC_LONG_CONTENT_EXECUTOR = None
_LAST = deque(maxlen=50)


class RuntimeBridgeUnavailable(RuntimeError):
    pass


class RuntimeBridgeCallError(RuntimeError):
    def __init__(self, message: str, *, code: str | None = None,
                 category: str | None = None, retryable: bool | None = None,
                 details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.category = category
        self.retryable = retryable
        self.details = details or {}


_SEMANTIC_HANDOFF_KEYS = {
    "recoverable_content_available",
    "content_ref",
    "content_hash",
    "content_length",
    "raw_finish_reason",
    "raw_usage",
    "request_max_tokens",
    "request_max_completion_tokens",
    "structured_output_capability",
    "structured_output_request",
    "response_format_type",
}


def _safe_semantic_handoff(error_details: Any, execution: dict[str, Any]) -> dict[str, Any]:
    """Normalize Runtime semantic-failure metadata without copying provider content.

    The public Runtime contract may evolve the container shape, so the Storage bridge
    accepts the handoff either directly under error.details.semantic_handoff or as
    provider evidence fields.  Only the allow-listed metadata is copied upward; the
    full Provider content must stay behind a controlled content reference.
    """
    candidates: list[dict[str, Any]] = []
    if isinstance(error_details, dict):
        for key in ("semantic_handoff", "handoff"):
            value = error_details.get(key)
            if isinstance(value, dict):
                candidates.append(value)
        candidates.append(error_details)
    provider_evidence = execution.get("provider_evidence") if isinstance(execution, dict) else None
    if isinstance(provider_evidence, dict):
        candidates.append(provider_evidence)

    out: dict[str, Any] = {}
    for candidate in candidates:
        for key in _SEMANTIC_HANDOFF_KEYS:
            if key in candidate and key not in out:
                out[key] = candidate[key]
    if out:
        out.setdefault("recoverable_content_available", bool(out.get("content_ref")))
    return out


def semantic_repair_handoff(error: RuntimeBridgeCallError) -> dict[str, Any] | None:
    """Return safe Runtime handoff metadata for SEMANTIC_REPAIR_REQUIRED only."""
    if error.code != "SEMANTIC_REPAIR_REQUIRED":
        return None
    value = error.details.get("semantic_handoff") if isinstance(error.details, dict) else None
    if not isinstance(value, dict):
        return None
    if value.get("recoverable_content_available") is not True:
        return None
    if not value.get("content_ref"):
        return None
    return dict(value)


def resolve_semantic_repair_content(error: RuntimeBridgeCallError) -> str:
    """Resolve Runtime-owned recoverable content without logging or persisting it.

    Runtime owns the content reference and resolver.  Storage only consumes the returned
    text transiently for a second structured extraction.  The test suite monkeypatches
    this adapter until the Runtime public Handoff contract lands.
    """
    handoff = semantic_repair_handoff(error)
    if handoff is None:
        raise RuntimeBridgeUnavailable("Runtime 未提供可用的 Semantic Failure Handoff")
    runtime, *_rest = _get_runtime()
    resolver = getattr(runtime, "read_semantic_handoff_content", None)
    if not callable(resolver):
        raise RuntimeBridgeUnavailable("Runtime 尚未提供 Semantic Handoff 读取接口")
    task_id = str(error.details.get("task_id") or "").strip()
    if not task_id:
        raise RuntimeBridgeUnavailable("Runtime Semantic Handoff 缺少 task_id")
    content = resolver(
        task_id=task_id,
        content_ref=handoff["content_ref"],
    )
    if not isinstance(content, str) or not content.strip():
        raise RuntimeBridgeUnavailable("Runtime content_ref 未解析到可用文本")
    return content


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def agent_config_dir() -> Path:
    """Canonical Storage-owned Runtime Agent configuration directory."""
    return (_project_root() / "config" / "runtime" / "agents").resolve()


def agent_config_path(agent_id: str) -> Path:
    allowed = {GENERIC_AGENT_ID, EMMC_PARAMETER_AGENT_ID}
    if agent_id not in allowed:
        raise RuntimeBridgeUnavailable(f"未知 Storage Parameter Agent：{agent_id}")
    return (agent_config_dir() / f"{agent_id}.yaml").resolve()


def knowledge_production_agent_config_path() -> Path:
    """Resolve the canonical shared Knowledge Production Agent contract.

    In the repository, Knowledge Production owns config/runtime/agents.
    A packaged candidate may stage that exact shared contract under its package
    root at the same relative path.  Storage never maintains a divergent copy.
    """
    product = _project_root()
    repository = product.parents[1] if len(product.parents) > 1 else product
    shared = repository / "config" / "runtime" / "agents" / f"{KNOWLEDGE_PRODUCTION_AGENT_ID}.yaml"
    if (repository / "knowledge_production").is_dir() and shared.is_file():
        return shared.resolve()
    packaged = product / "config" / "runtime" / "agents" / f"{KNOWLEDGE_PRODUCTION_AGENT_ID}.yaml"
    if packaged.is_file():
        return packaged.resolve()
    raise RuntimeBridgeUnavailable(
        "Knowledge Production canonical Agent 配置缺失："
        f"checked={shared},{packaged}"
    )


def execution_mode_source() -> str:
    return "environment" if "STORAGE_LIFE_EXECUTION_MODE" in os.environ else "legacy_compat_default"


def route_for_device_type(device_type: str) -> dict[str, Any]:
    """Return the frozen four-family parameter-extraction route.

    eMMC owns a dedicated 37-field Runtime contract.  The other three families use
    the generic Runtime JSON agent together with their device-specific schema/read
    plan/semantic rules.  This function is intentionally deterministic and does not
    inspect Provider or model state.
    """
    from . import templates

    normalized = templates.normalize_device_type(device_type)
    if normalized not in FOUR_FAMILY_DEVICE_TYPES:
        raise RuntimeBridgeUnavailable(f"不支持的存储器件类型：{device_type}")
    dedicated = normalized == "eMMC"
    agent_id = EMMC_PARAMETER_AGENT_ID if dedicated else GENERIC_AGENT_ID
    return {
        "device_type": normalized,
        "agent_id": agent_id,
        "agent_config_path": str(agent_config_path(agent_id)),
        "dedicated_agent": dedicated,
        "business_contract": (
            "StorageParameterExtractResultV1" if dedicated else "StorageDynamicJson"
        ),
        "specialization_source": "config/spec_templates.yaml",
    }


def routing_table() -> dict[str, dict[str, Any]]:
    return {device_type: route_for_device_type(device_type) for device_type in FOUR_FAMILY_DEVICE_TYPES}


def knowledge_production_boundary() -> dict[str, Any]:
    return {
        "agent_id": KNOWLEDGE_PRODUCTION_AGENT_ID,
        "agent_config_path": str(knowledge_production_agent_config_path()),
        "scope": "GENERIC_KNOWLEDGE_PRODUCTION",
        "device_context": "preserved_as_input_context",
        "emmc_only": False,
    }


def model_config_path(runtime_root_path: Path) -> Path:
    """Resolve the single effective Storage Runtime model configuration.

    User override is explicit via STORAGE_MODEL_CONFIG.  Without an override,
    Storage always uses its own package/source config/model.local.yaml.  Runtime's
    public model.yaml is a platform template and is not a silent Storage fallback.
    """
    explicit = os.environ.get("STORAGE_MODEL_CONFIG", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = (_project_root() / path).resolve()
        else:
            path = path.resolve()
        source = "user"
    else:
        path = (_project_root() / "config" / "model.local.yaml").resolve()
        source = "storage-default"
    if not path.is_file():
        raise RuntimeBridgeUnavailable(
            f"Storage Runtime model config 不存在：{path} (source={source})"
        )
    return path


def execution_mode() -> str:
    value = os.environ.get("STORAGE_LIFE_EXECUTION_MODE", "legacy").strip().lower()
    if value not in {"legacy", "runtime"}:
        raise RuntimeBridgeUnavailable(
            "STORAGE_LIFE_EXECUTION_MODE 仅支持 legacy/runtime"
        )
    return value


def runtime_root() -> Path | None:
    explicit = os.environ.get("UNIFIED_AGENT_RUNTIME_ROOT", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    project = _project_root()
    candidates = [
        project.parent / "ploblem_ai",
        project.parent / "unified_agent_runtime",
        project / ".external" / "ploblem_ai",
        project / "external" / "ploblem_ai",
    ]
    for candidate in candidates:
        if (candidate / "runtime" / "__init__.py").exists():
            return candidate.resolve()
    return None


def _git_head(root: Path) -> str | None:
    if not (root / ".git").exists():
        return None
    try:
        p = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=True,
        )
        return p.stdout.strip()
    except Exception:
        return None


def _verify_runtime_root(root: Path) -> dict[str, Any]:
    missing = [
        str(x.relative_to(root))
        for x in [
            root / "runtime" / "__init__.py",
            root / "config" / "runtime" / "model.yaml",
            root / "tools" / "openai_mock" / "server.py",
        ]
        if not x.exists()
    ]
    if missing:
        raise RuntimeBridgeUnavailable(
            "Unified Runtime 路径不完整，缺少：" + ", ".join(missing)
        )
    head = _git_head(root)
    marker = root / "RUNTIME_COMMIT"
    snapshot_commit = marker.read_text(encoding="utf-8").strip() if marker.is_file() else None
    actual = head or snapshot_commit
    allow_unpinned = os.environ.get("STORAGE_LIFE_ALLOW_UNPINNED_RUNTIME", "0") == "1"
    if actual and actual != RUNTIME_EXPECTED_COMMIT and not allow_unpinned:
        raise RuntimeBridgeUnavailable(
            f"Runtime commit 不匹配：expected={RUNTIME_EXPECTED_COMMIT}, actual={actual}"
        )
    if not actual and not allow_unpinned:
        raise RuntimeBridgeUnavailable("Runtime 无法校验版本：缺少 .git 与 RUNTIME_COMMIT")
    return {
        "root": str(root),
        "head": head,
        "snapshot_commit": snapshot_commit,
        "expected_commit": RUNTIME_EXPECTED_COMMIT,
        "pinned": actual == RUNTIME_EXPECTED_COMMIT,
    }


def _ensure_runtime_import(root: Path) -> None:
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


def _build_runtime():
    root = runtime_root()
    if root is None:
        raise RuntimeBridgeUnavailable(
            "未找到 Unified Agent Runtime。请设置 UNIFIED_AGENT_RUNTIME_ROOT 指向公共 Runtime 仓库。"
        )
    runtime_info = _verify_runtime_root(root)
    _ensure_runtime_import(root)

    try:
        import runtime as runtime_package
        from runtime import AgentConfigLoader, AgentRequest, ConfiguredAgentRuntime, RuntimeStatus, SqliteTaskStore
    except Exception as exc:
        raise RuntimeBridgeUnavailable(f"Unified Runtime 导入失败：{type(exc).__name__}: {exc}") from exc

    runtime_module = Path(runtime_package.__file__).resolve()
    expected_runtime_module = (root / "runtime" / "__init__.py").resolve()
    if runtime_module != expected_runtime_module:
        raise RuntimeBridgeUnavailable(
            "Runtime import source 不匹配："
            f"expected={expected_runtime_module}, actual={runtime_module}"
        )

    from .runtime_domain_strategy import StorageEmmcDomainStrategy

    project = _project_root()
    store_path = Path(
        os.environ.get(
            "STORAGE_LIFE_RUNTIME_DB",
            str(project / "data" / "runtime_tasks.sqlite3"),
        )
    ).expanduser()
    store_path.parent.mkdir(parents=True, exist_ok=True)

    model_config = model_config_path(root)
    loader = AgentConfigLoader(
        root=project,
        model_profiles=model_config,
        schemas={
            "StorageDynamicJson": {"type": "object"},
            "StorageParameterExtractResultV1": {"type": "object"},
        },
        content_strategies={
            StorageEmmcDomainStrategy.strategy_ref: StorageEmmcDomainStrategy,
        },
        environ=os.environ,
    )
    runtime = ConfiguredAgentRuntime(
        SqliteTaskStore(store_path),
        config_loader=loader,
    )
    # Runtime main now owns OpenAI-compatible Provider execution.  Storage must not
    # inject a second HTTP handler here, otherwise provider execution would fork.
    generic_agent_config = agent_config_path(GENERIC_AGENT_ID)
    emmc_agent_config = agent_config_path(EMMC_PARAMETER_AGENT_ID)
    missing_agent_configs = [
        str(path) for path in (generic_agent_config, emmc_agent_config)
        if not path.is_file()
    ]
    if missing_agent_configs:
        raise RuntimeBridgeUnavailable(
            "Storage canonical Agent 配置缺失：" + ", ".join(missing_agent_configs)
        )
    resolved_generic = runtime.load_agent(generic_agent_config)
    resolved_emmc = runtime.load_agent(emmc_agent_config)
    resolved = {
        GENERIC_AGENT_ID: resolved_generic,
        EMMC_PARAMETER_AGENT_ID: resolved_emmc,
    }
    runtime_info = {
        **runtime_info,
        "model_config": str(model_config),
        "runtime_module": str(runtime_module),
        "agent_configs": {
            GENERIC_AGENT_ID: str(generic_agent_config),
            EMMC_PARAMETER_AGENT_ID: str(emmc_agent_config),
        },
    }
    return runtime, resolved, AgentRequest, RuntimeStatus, runtime_info, store_path


def reset_for_tests() -> None:
    global _RUNTIME, _EMMC_LONG_CONTENT_EXECUTOR
    with _LOCK:
        _RUNTIME = None
        _EMMC_LONG_CONTENT_EXECUTOR = None
        _LAST.clear()


def _get_runtime():
    global _RUNTIME
    with _LOCK:
        if _RUNTIME is None:
            _RUNTIME = _build_runtime()
        return _RUNTIME


def _get_emmc_long_content_executor():
    """Bind the frozen eMMC strategy to Runtime's public recovery executor."""
    global _EMMC_LONG_CONTENT_EXECUTOR
    with _LOCK:
        if _EMMC_LONG_CONTENT_EXECUTOR is not None:
            return _EMMC_LONG_CONTENT_EXECUTOR
        runtime, _resolved, _AgentRequest, _RuntimeStatus, _info, _store_path = _get_runtime()
        from runtime.content import LongContentRecoveryExecutor

        executor = LongContentRecoveryExecutor(
            runtime,
            runtime.store,
            chunk_payload_builder=_emmc_chunk_payload_builder,
            merger=_StorageEmmcResultMerger(),
            final_validator=_emmc_final_validator,
            business_gate=_emmc_business_gate,
        )
        executor.bind_configured_agent(agent_config_path(EMMC_PARAMETER_AGENT_ID))
        provider_handler = executor.provider_handler

        def validate_chunk_result(provider_payload, context):
            return _validate_emmc_chunk_result(
                provider_handler(provider_payload, context),
                provider_payload,
            )

        executor.provider_handler = validate_chunk_result
        _EMMC_LONG_CONTENT_EXECUTOR = executor
        return executor


def _emmc_source_text(payload: dict[str, Any]) -> str:
    text = payload.get("page_text")
    if isinstance(text, str) and text.strip():
        return text
    pages = payload.get("pages")
    if not isinstance(pages, list) or not pages:
        raise RuntimeBridgeCallError(
            "eMMC long-content binding requires source pages/page_text",
            code="STORAGE_EMMC_LONG_CONTENT_SOURCE_MISSING",
            category="VALIDATION",
            retryable=False,
        )
    return "\n\n".join(
        f"SOURCE {item.get('source_id') or ''} PAGE {item.get('page') or ''}\n{item.get('text') or ''}"
        for item in pages
        if isinstance(item, dict)
    )


def _emmc_chunk_schema(schema: dict[str, Any], field_keys: list[str]) -> dict[str, Any]:
    """Narrow the existing eMMC output contract without changing its shape."""
    narrowed = json.loads(json.dumps(schema))
    fields = narrowed.get("properties", {}).get("fields")
    if not isinstance(fields, dict) or not isinstance(fields.get("items"), dict):
        raise RuntimeBridgeCallError(
            "eMMC output schema is incompatible with StorageParameterExtractResultV1",
            code="STORAGE_EMMC_LONG_CONTENT_SCHEMA_INVALID",
            category="VALIDATION",
            retryable=False,
        )
    fields["minItems"] = len(field_keys)
    fields["maxItems"] = len(field_keys)
    fields["items"].setdefault("properties", {}).setdefault("field_key", {})["enum"] = list(field_keys)
    return narrowed


def _emmc_chunk_payload_builder(bundle, _plan, chunk, _context):
    request = bundle.shared_context.get("storage_provider_request") or {}
    instructions = request.get("instructions")
    provider_payload = request.get("provider_payload")
    schema = request.get("schema")
    if not isinstance(instructions, str) or not isinstance(provider_payload, dict) or not isinstance(schema, dict):
        raise RuntimeBridgeCallError(
            "eMMC Runtime bundle is missing the provider request contract",
            code="STORAGE_EMMC_LONG_CONTENT_REQUEST_MISSING",
            category="VALIDATION",
            retryable=False,
        )
    unit_by_id = {unit.unit_id: unit for unit in bundle.logical_units}
    field_keys = [
        str(unit_by_id[unit_id].metadata.get("storage_field_key") or "")
        for unit_id in chunk.unit_ids
        if unit_id in unit_by_id
    ]
    if not field_keys or any(not key for key in field_keys):
        raise RuntimeBridgeCallError(
            "Runtime produced an eMMC chunk without Storage field keys",
            code="STORAGE_EMMC_LONG_CONTENT_FIELDS_MISSING",
            category="VALIDATION",
            retryable=False,
        )
    chunk_provider_payload = dict(provider_payload)
    chunk_provider_payload["target_fields"] = field_keys
    return {
        "instructions": instructions,
        "provider_payload": chunk_provider_payload,
        "schema": _emmc_chunk_schema(schema, field_keys),
    }


def _validate_emmc_chunk_result(result: Any, request: dict[str, Any]) -> dict[str, Any]:
    from jsonschema import validate as validate_json_schema
    from runtime.contracts import ErrorCategory
    from runtime.reliability.errors import RuntimeStepError

    schema = request.get("schema")
    try:
        validate_json_schema(result, schema)
    except Exception as exc:
        raise RuntimeStepError(
            "eMMC provider chunk failed its Runtime-bound group schema",
            code="PROVIDER_SCHEMA_INVALID",
            category=ErrorCategory.VALIDATION,
            retryable=True,
            details={"schema_validation_error": type(exc).__name__},
        ) from exc
    requested = list((request.get("provider_payload") or {}).get("target_fields") or [])
    fields = result.get("fields") if isinstance(result, dict) else None
    returned = [
        str(item.get("field_key") or "").strip()
        for item in fields or []
        if isinstance(item, dict)
    ]
    if (
        not isinstance(fields, list)
        or len(returned) != len(fields)
        or len(returned) != len(requested)
        or set(returned) != set(requested)
        or len(set(returned)) != len(returned)
    ):
        raise RuntimeStepError(
            "eMMC provider chunk must return each requested field exactly once",
            code="PROVIDER_SCHEMA_INVALID",
            category=ErrorCategory.VALIDATION,
            retryable=True,
            details={"requested_fields": requested, "returned_fields": returned},
        )
    return result


class _StorageEmmcResultMerger:
    """Adapt the frozen Storage merge into Runtime's public MergeResult contract."""

    def merge(self, inputs, context):
        from runtime.contracts import MergeResult
        from .runtime_domain_strategy import StorageEmmcDomainStrategy

        merged = StorageEmmcDomainStrategy.merge_partials(inputs)
        missing = list(merged.get("missing_field_keys") or [])
        return MergeResult(
            merge_key=context.merge_key,
            data=merged,
            derived_from_partial_ids=[item.partial_id for item in inputs],
            complete=not missing and not any(
                str(item.get("type") or "").startswith("invalid_")
                for item in merged.get("merge_conflicts") or []
            ),
            missing_partial_ids=[],
            warnings=["STORAGE_EMMC_SEMANTIC_CONFLICT"] if merged.get("merge_conflicts") else [],
            metadata={"business_domain": "STORAGE", "field_count": merged.get("field_count")},
        )


def _emmc_final_validator(merged: Any) -> bool:
    from .runtime_domain_strategy import EMMC_FIELD_ORDER

    return (
        isinstance(merged, dict)
        and len(merged.get("fields") or []) == len(EMMC_FIELD_ORDER)
        and not merged.get("missing_field_keys")
        and not any(str(item.get("type") or "").startswith("invalid_") for item in merged.get("merge_conflicts") or [])
    )


def _emmc_business_gate(merge, coverages, _bundle, _plan) -> bool:
    from .runtime_domain_strategy import StorageEmmcDomainStrategy

    if merge is None or not coverages or not all(item.complete for item in coverages):
        return False
    gate = StorageEmmcDomainStrategy.business_gate(
        merge.data,
        evidence_resolved=False,
        review_resolved=False,
    )
    # Runtime establishes complete field coverage and preserves review/evidence
    # findings; Storage's existing evidence resolver and human-review path consume
    # those findings after the extraction result returns.
    return not any(
        reason not in {"BUSINESS_REVIEW_REQUIRED", "EVIDENCE_NOT_RESOLVED"}
        for reason in gate["reasons"]
    )


def _call_emmc_long_content(
    instructions: str,
    payload: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    from .runtime_domain_strategy import EMMC_FIELD_ORDER, StorageEmmcDomainStrategy

    requested = schema.get("properties", {}).get("fields", {}).get("items", {}).get("properties", {}).get("field_key", {}).get("enum", [])
    if list(requested) != list(EMMC_FIELD_ORDER):
        raise RuntimeBridgeCallError(
            "eMMC long-content binding applies only to the frozen 37-field extraction contract",
            code="STORAGE_EMMC_LONG_CONTENT_CONTRACT_MISMATCH",
            category="VALIDATION",
            retryable=False,
        )

    source_id = str(payload.get("primary_source_id") or "").strip()
    pages = payload.get("pages")
    if not source_id and isinstance(pages, list):
        source_id = next(
            (str(item.get("source_id") or "").strip() for item in pages if isinstance(item, dict) and item.get("source_id")),
            "",
        )
    if not source_id:
        raise RuntimeBridgeCallError(
            "eMMC long-content binding requires primary_source_id",
            code="STORAGE_EMMC_LONG_CONTENT_SOURCE_ID_MISSING",
            category="VALIDATION",
            retryable=False,
        )
    source_text = _emmc_source_text(payload)
    fingerprint = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    descriptor = StorageEmmcDomainStrategy.descriptor(
        source_ref={
            "source_id": source_id,
            "source_type": "STORAGE_DATASHEET",
            "content_hash": fingerprint,
            "fingerprint": fingerprint,
            "uri": f"storage-source:{source_id}",
        },
        source_text=source_text,
        schema_version="emmc_schema_v0.2",
    )
    bundle = StorageEmmcDomainStrategy.to_runtime_bundle(descriptor)
    bundle.shared_context["storage_provider_request"] = {
        "instructions": instructions,
        "provider_payload": payload,
        "schema": schema,
    }
    executor = _get_emmc_long_content_executor()
    request_fingerprint = hashlib.sha256(
        json.dumps(
            {"instructions": instructions, "payload": payload, "schema": schema,
             "strategy_ref": StorageEmmcDomainStrategy.strategy_ref},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    request_id = "storage-emmc-lc-" + request_fingerprint
    existing_task = executor.store.get_task_by_request_id(request_id)
    metadata = {
        "business_domain": "STORAGE",
        "storage_execution_mode": "runtime_long_content",
        "storage_agent_id": EMMC_PARAMETER_AGENT_ID,
        "source_id": source_id,
        "source_fingerprint": fingerprint,
    }
    if existing_task is None:
        outcome = executor.execute(
            bundle,
            request_id=request_id,
            strategy_ref=StorageEmmcDomainStrategy.strategy_ref,
            metadata=metadata,
        )
    elif str(existing_task.status.value) in {"PARTIAL", "RUNNING"}:
        outcome = executor.resume(existing_task.task_id)
    elif str(existing_task.status.value) == "COMPLETED":
        outcome = executor.outcome(existing_task.task_id)
    else:
        raise RuntimeBridgeCallError(
            "Existing Runtime eMMC long-content task is not resumable",
            code="STORAGE_EMMC_LONG_CONTENT_TASK_NOT_RESUMABLE",
            category="EXECUTION",
            retryable=False,
            details={"task_id": existing_task.task_id, "status": existing_task.status.value},
        )
    _LAST.append({
        "agent_id": EMMC_PARAMETER_AGENT_ID,
        "request_id": request_id,
        "task_id": outcome.task_id,
        "run_id": outcome.run_id,
        "status": outcome.status.value,
        "runtime_status": outcome.runtime_status.value,
        "provider_calls": outcome.provider_calls,
        "runtime_long_content": True,
        "plan_id": outcome.plan_id,
        "source_id": source_id,
        "source_fingerprint": fingerprint,
        "committed_partial_count": len(outcome.committed_partials),
        "business_consumable": outcome.business_consumable,
    })
    if not outcome.business_consumable or outcome.merge is None or not isinstance(outcome.merge.data, dict):
        raise RuntimeBridgeCallError(
            "Runtime eMMC long-content execution is incomplete",
            code="STORAGE_EMMC_LONG_CONTENT_INCOMPLETE",
            category="EXECUTION",
            retryable=False,
            details={
                "request_id": request_id,
                "task_id": outcome.task_id,
                "status": outcome.status.value,
                "runtime_status": outcome.runtime_status.value,
                "plan_id": outcome.plan_id,
                "committed_partial_count": len(outcome.committed_partials),
                "gate": outcome.gate.model_dump(mode="json"),
            },
        )
    merged = outcome.merge.data
    if merged.get("missing_field_keys") or len(merged.get("fields") or []) != len(EMMC_FIELD_ORDER):
        raise RuntimeBridgeCallError(
            "Runtime eMMC long-content merge did not return exactly 37 fields",
            code="STORAGE_EMMC_LONG_CONTENT_MERGE_INVALID",
            category="VALIDATION",
            retryable=False,
            details={"task_id": outcome.task_id, "missing_field_keys": merged.get("missing_field_keys")},
        )
    return merged


def configured() -> bool:
    if execution_mode() != "runtime":
        return False
    try:
        _get_runtime()
        return True
    except RuntimeBridgeUnavailable:
        return False


def status(device_type: str | None = None) -> dict[str, Any]:
    routes = routing_table()
    active_route = route_for_device_type(device_type) if device_type else None
    mode = execution_mode()
    base = {
        "configured": False,
        "execution_mode": mode,
        "execution_mode_source": execution_mode_source(),
        "runtime_expected_commit": RUNTIME_EXPECTED_COMMIT,
        "runtime_root": str(runtime_root()) if runtime_root() else None,
        "canonical_agent_config_dir": str(agent_config_dir()),
        "provider": None,
        "profile": None,
        "model": None,
        "max_output_tokens": None,
        "json_mode": True,
        "agents": [],
        "agent_details": {},
        "routing": routes,
        "active_route": active_route,
        "knowledge_production": knowledge_production_boundary(),
        "legacy_config_policy": {
            "path": str((_project_root() / "config" / "agent.yaml").resolve()),
            "runtime_reads_legacy_config": False,
            "compatibility_only": True,
        },
        "last_execution": list(_LAST)[-1] if _LAST else None,
    }
    if mode != "runtime":
        return {
            **base,
            "warning": (
                "LEGACY_COMPATIBILITY_MODE_EXPLICIT"
                if execution_mode_source() == "environment"
                else "LEGACY_COMPATIBILITY_DEFAULT_NOT_FOR_RELEASE_LAUNCHER"
            ),
        }
    try:
        _runtime, resolved_map, _AgentRequest, _RuntimeStatus, runtime_info, store_path = _get_runtime()
    except Exception as exc:
        return {**base, "error": f"{type(exc).__name__}: {exc}"}

    agent_details: dict[str, dict[str, Any]] = {}
    for agent_id, resolved in resolved_map.items():
        agent_details[agent_id] = {
            "agent_id": agent_id,
            "agent_config_path": (runtime_info.get("agent_configs") or {}).get(agent_id),
            "model_ref": resolved.provider.profile_ref,
            "provider": resolved.provider.type,
            "model": resolved.provider.model,
            "base_url": resolved.provider.base_url,
            "base_url_env": resolved.provider.base_url_env,
            "api_key_env": resolved.provider.api_key_env,
            "max_output_tokens": resolved.execution_policy.model_policy.get("max_tokens"),
        }

    # Generic is the neutral top-level status surface.  eMMC is a device-specific
    # dedicated route and must not appear to be the default for all Storage devices.
    primary = resolved_map[GENERIC_AGENT_ID]
    try:
        resolved_secret = _runtime.config_loader.get_runtime_api_key(
            primary.config_hash,
            api_key_env=primary.provider.api_key_env,
        )
    except Exception:
        resolved_secret = None

    resolved_active_route = dict(active_route) if active_route else None
    if resolved_active_route:
        detail = agent_details.get(resolved_active_route["agent_id"]) or {}
        resolved_active_route.update({
            "model_ref": detail.get("model_ref"),
            "provider": detail.get("provider"),
            "model": detail.get("model"),
        })

    return {
        **base,
        "configured": True,
        "provider": primary.provider.type,
        "profile": primary.provider.profile_ref,
        "model": primary.provider.model,
        "base_url": primary.provider.base_url,
        "base_url_env": primary.provider.base_url_env,
        "api_key_env": primary.provider.api_key_env,
        "api_key_present": bool(resolved_secret),
        "max_output_tokens": primary.execution_policy.model_policy.get("max_tokens"),
        "runtime": runtime_info,
        "runtime_db": str(store_path),
        "agents": list(resolved_map.keys()),
        "agent_details": agent_details,
        "active_route": resolved_active_route,
    }


def last_executions() -> list[dict[str, Any]]:
    return list(_LAST)


def _invoke_json(agent_id: str, instructions: str, payload: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    runtime, resolved_map, AgentRequest, RuntimeStatus, runtime_info, _store_path = _get_runtime()
    resolved = resolved_map[agent_id]
    request_id = "storage-" + uuid4().hex
    request = AgentRequest(
        request_id=request_id,
        agent_id=agent_id,
        input={
            "instructions": instructions,
            "provider_payload": payload,
            "schema": schema,
        },
        metadata={
            "business_domain": "STORAGE",
            "business_id": request_id,
            "storage_execution_mode": "runtime",
            "storage_agent_id": agent_id,
        },
    )
    result = runtime.invoke(request)
    execution = result.execution.model_dump(mode="json") if hasattr(result.execution, "model_dump") else {}
    event = {
        "agent_id": agent_id,
        "request_id": request_id,
        "task_id": result.task_id,
        "run_id": result.run_id,
        "status": result.status.value if hasattr(result.status, "value") else str(result.status),
        "provider_calls": execution.get("provider_calls"),
        "retry_budget_exhausted": execution.get("retry_budget_exhausted"),
        "duration_ms": execution.get("duration_ms"),
        "provider": execution.get("provider") or resolved.provider.type,
        "model": execution.get("model") or resolved.provider.model,
        "runtime_commit": runtime_info.get("head") or runtime_info.get("expected_commit"),
    }
    if result.error is not None:
        err = result.error.model_dump(mode="json") if hasattr(result.error, "model_dump") else {}
        semantic_handoff = _safe_semantic_handoff(err.get("details"), execution)
        event["error"] = {
            "code": err.get("code"),
            "category": err.get("category"),
            "message": err.get("message"),
            "retryable": err.get("retryable"),
        }
        if err.get("code") == "SEMANTIC_REPAIR_REQUIRED" and semantic_handoff:
            event["error"]["semantic_handoff"] = semantic_handoff
    _LAST.append(event)

    if result.status != RuntimeStatus.COMPLETED:
        err = event.get("error") or {}
        details = {"request_id": request_id, "task_id": result.task_id, "execution": execution}
        if isinstance(err.get("semantic_handoff"), dict):
            details["semantic_handoff"] = dict(err["semantic_handoff"])
        raise RuntimeBridgeCallError(
            err.get("message") or f"Runtime execution failed: {event['status']}",
            code=err.get("code"),
            category=err.get("category"),
            retryable=err.get("retryable"),
            details=details,
        )
    if not isinstance(result.data, dict):
        raise RuntimeBridgeCallError(
            "Runtime returned non-object Storage AI result",
            code="STORAGE_RUNTIME_RESULT_INVALID",
            category="VALIDATION",
            retryable=False,
            details={"request_id": request_id, "type": type(result.data).__name__},
        )
    return result.data


def call_json(instructions: str, payload: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    return _invoke_json(GENERIC_AGENT_ID, instructions, payload, schema)


def call_parameter_extract(instructions: str, payload: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Run the frozen eMMC 37-field business extraction through its dedicated Runtime agent."""
    route = None
    if str(payload.get("device_type") or "").strip():
        route = route_for_device_type(str(payload["device_type"]))
    if (
        route is not None
        and route.get("agent_id") == EMMC_PARAMETER_AGENT_ID
        and bool(str(payload.get("primary_source_id") or "").strip())
        and isinstance(payload.get("pages"), list)
        and bool(payload.get("pages"))
        and isinstance(payload.get("page_text"), str)
        and bool(payload.get("page_text", "").strip())
        and isinstance(schema, dict)
        and schema.get("properties", {}).get("fields", {}).get("items", {}).get("properties", {}).get("field_key", {}).get("enum")
    ):
        from .runtime_domain_strategy import EMMC_FIELD_ORDER

        requested = schema["properties"]["fields"]["items"]["properties"]["field_key"]["enum"]
        if list(requested) == list(EMMC_FIELD_ORDER):
            return _call_emmc_long_content(instructions, payload, schema)
    return _invoke_json(EMMC_PARAMETER_AGENT_ID, instructions, payload, schema)
