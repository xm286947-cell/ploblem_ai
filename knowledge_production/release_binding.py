"""Fail-closed validation for a pinned Knowledge Release binding."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class ReleaseBindingError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


REQUIRED_BINDING = {
    "storage_product_version": "STORAGE_PRODUCT_MVP_RC1",
    "knowledge_release_version": "KP-STORAGE-RC1-VALIDATION-001",
    "knowledge_object_contract_version": "knowledge-object/v1",
    "knowledge_query_contract_version": "knowledge-query/v1",
    "common_evidence_contract_version": "common-evidence/v1.0",
    "storage_consumer_contract_version": "UKCI-01/V1.0",
}


def validate_release_binding(
    binding: Mapping[str, Any],
    *,
    release_manifest: Mapping[str, Any] | None = None,
) -> None:
    """Validate product/release/contract identity before consumer startup."""

    if not isinstance(binding, Mapping):
        raise ReleaseBindingError("RELEASE_BINDING_INVALID")
    if binding.get("binding_contract_version") != "knowledge-release-binding/v1.0":
        raise ReleaseBindingError("RELEASE_BINDING_INVALID", "binding contract version")
    for field, expected in REQUIRED_BINDING.items():
        if binding.get(field) != expected:
            raise ReleaseBindingError("RELEASE_VERSION_MISMATCH", field)
    if binding.get("compatibility_status") != "PASS":
        raise ReleaseBindingError("RELEASE_BINDING_INVALID", "compatibility status")
    if binding.get("latest_floating_dependency") is not False:
        raise ReleaseBindingError("LATEST_FLOATING_DEPENDENCY")
    for flag in ("direct_knowledge_db_access", "candidate_store_access", "storage_self_publish"):
        if binding.get(flag) is not False:
            raise ReleaseBindingError("FORBIDDEN_STORAGE_COUPLING", flag)

    required_behavior = binding.get("required_behavior")
    if not isinstance(required_behavior, Mapping) or any(
        required_behavior.get(flag) is not True
        for flag in (
            "pinned_release_on_startup",
            "fail_closed_on_version_mismatch",
            "fail_closed_on_missing_release",
            "fail_closed_on_evidence_contract_mismatch",
            "upgrade_requires_new_binding",
            "rollback_requires_previous_binding",
        )
    ):
        raise ReleaseBindingError("RELEASE_BINDING_INVALID", "required behavior")

    if release_manifest is None:
        raise ReleaseBindingError("KNOWLEDGE_RELEASE_NOT_FOUND")
    if not isinstance(release_manifest, Mapping):
        raise ReleaseBindingError("KNOWLEDGE_RELEASE_INVALID")
    if release_manifest.get("knowledge_release_version") != REQUIRED_BINDING["knowledge_release_version"]:
        raise ReleaseBindingError("RELEASE_VERSION_MISMATCH", "release version")
    if release_manifest.get("contract_version") != REQUIRED_BINDING["knowledge_query_contract_version"]:
        raise ReleaseBindingError("RELEASE_VERSION_MISMATCH", "query contract")
    if release_manifest.get("object_contract_version") != REQUIRED_BINDING["knowledge_object_contract_version"]:
        raise ReleaseBindingError("RELEASE_VERSION_MISMATCH", "object contract")
