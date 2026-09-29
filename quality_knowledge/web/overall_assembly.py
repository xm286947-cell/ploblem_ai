"""Platform-owned Overall domain assembly registry.

This module describes composition only. It never imports a Domain repository,
model, table or database. Runtime readiness is derived from binding metadata
already exposed on the composed FastAPI app state.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


ASSEMBLY_CONTRACT_VERSION = "overall-domain-assembly/v1"

DOMAIN_ASSEMBLY_SPECS: tuple[dict[str, Any], ...] = (
    {
        "domain_id": "major",
        "title": "重大问题案例库 × Repeat Risk",
        "owner": "Major Problem / Historical Case Domain",
        "entry_path": "/p0/cases",
        "binding_attr": "historical_case_service",
        "binding_kind": "PUBLIC_SERVICE",
        "public_contracts": (
            "historical-case/v1",
            "major-problem-context/v1",
        ),
    },
    {
        "domain_id": "hardware",
        "title": "硬件案例库",
        "owner": "Hardware Case Domain",
        "entry_path": "/p0/hardware-cases",
        "binding_attr": "hardware_case_service",
        "binding_kind": "PUBLIC_SERVICE",
        "public_contracts": ("hardware-case/v1",),
    },
    {
        "domain_id": "quality-scenario",
        "title": "质量场景库 / 画像",
        "owner": "Quality Scenario Domain",
        "entry_path": "/p0/quality-scenario-insights",
        "binding_attr": "p04_service",
        "binding_kind": "PUBLIC_SERVICE",
        "public_contracts": ("quality-scenario-insight/v1",),
        "consumed_contracts": ("major-problem-context/v1",),
    },
    {
        "domain_id": "storage",
        "title": "存储器件寿命智能产品",
        "owner": "Storage Product Domain",
        "entry_path": "/storage-workspace/",
        "binding_attr": "storage_workspace_binding",
        "binding_kind": "ASGI_MOUNT_EXISTING_APP",
        "public_contracts": (
            "knowledge-query/v1",
            "knowledge-evidence/v1",
        ),
    },
)


def _get_state_value(state: Any, name: str) -> Any:
    if isinstance(state, Mapping):
        return state.get(name)
    return getattr(state, name, None)


def _is_bound(spec: Mapping[str, Any], value: Any) -> bool:
    if spec.get("binding_kind") == "ASGI_MOUNT_EXISTING_APP":
        return isinstance(value, Mapping) and bool(value.get("prefix"))
    return value is not None


def build_domain_assembly(state: Any) -> dict[str, Any]:
    """Project platform binding metadata into the public assembly contract."""

    items: list[dict[str, Any]] = []
    for raw in DOMAIN_ASSEMBLY_SPECS:
        spec = dict(raw)
        value = _get_state_value(state, str(spec["binding_attr"]))
        bound = _is_bound(spec, value)
        item = {
            "domain_id": spec["domain_id"],
            "title": spec["title"],
            "owner": spec["owner"],
            "entry_path": spec["entry_path"],
            "binding_kind": spec["binding_kind"],
            "state": "READY" if bound else "UNBOUND",
            "bound": bound,
            "public_contracts": list(spec.get("public_contracts") or ()),
            "consumed_contracts": list(spec.get("consumed_contracts") or ()),
            "evidence_contract": "common-evidence/v1.0",
            "return_contract": "overall-return-context/v1",
            "data_ownership": "DOMAIN_OWNED",
            "overall_direct_repository_access": False,
            "fail_closed": True,
        }
        if spec["domain_id"] == "storage" and isinstance(value, Mapping):
            item["mount_prefix"] = value.get("prefix")
        items.append(item)

    ready = sum(1 for item in items if item["bound"])
    return {
        "contract_version": ASSEMBLY_CONTRACT_VERSION,
        "items": items,
        "total": len(items),
        "ready": ready,
        "unbound": len(items) - ready,
        "all_ready": ready == len(items),
        "composition_policy": "PUBLIC_CONTRACT_OR_EXISTING_APP_MOUNT_ONLY",
        "direct_domain_repository_access": False,
    }
