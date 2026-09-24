"""HC-TREE-IMPORT-001 M1 contract for versioned Hardware Tree import.

This module freezes the product-visible import/version/change semantics. Excel
parsing and diff generation are intentionally deferred to HC-TREE-M2.
"""
from __future__ import annotations

from pathlib import PurePath
from typing import Any

from services.hardware_case_contract import TREE_TYPES


CONTRACT_VERSION = "hardware-tree-import/v1"

IMPORT_TYPES = frozenset({"INITIAL_IMPORT", "UPDATE_IMPORT"})
IMPORT_STATUSES = frozenset(
    {
        "UPLOADED",
        "PARSED",
        "MAPPING_REQUIRED",
        "VALIDATING",
        "REVIEW_REQUIRED",
        "READY_TO_APPLY",
        "APPLYING",
        "APPLIED",
        "APPLIED_WITH_EXCLUSIONS",
        "PARSE_FAILED",
        "VALIDATION_FAILED",
        "APPLY_FAILED",
    }
)
VERSION_STATUSES = frozenset({"ACTIVE", "SUPERSEDED"})
CHANGE_TYPES = frozenset(
    {"ADD", "UPDATE", "RENAME", "MOVE", "NO_CHANGE", "CONFLICT", "DEPRECATE"}
)
CHANGE_DECISIONS = frozenset({"PENDING", "CONFIRMED", "EXCLUDED", "RESOLVED"})

MUTATING_CHANGE_TYPES = frozenset({"ADD", "UPDATE", "RENAME", "MOVE", "DEPRECATE"})
TERMINAL_IMPORT_STATUSES = frozenset(
    {
        "APPLIED",
        "APPLIED_WITH_EXCLUSIONS",
        "PARSE_FAILED",
        "VALIDATION_FAILED",
        "APPLY_FAILED",
    }
)

STATUS_TRANSITIONS = {
    "UPLOADED": {"PARSED", "PARSE_FAILED"},
    "PARSED": {"MAPPING_REQUIRED", "VALIDATING"},
    "MAPPING_REQUIRED": {"VALIDATING"},
    "VALIDATING": {"REVIEW_REQUIRED", "VALIDATION_FAILED"},
    "REVIEW_REQUIRED": {"READY_TO_APPLY", "VALIDATING"},
    "READY_TO_APPLY": {"APPLYING"},
    "APPLYING": {"APPLIED", "APPLIED_WITH_EXCLUSIONS", "APPLY_FAILED"},
    "PARSE_FAILED": {"UPLOADED"},
    "VALIDATION_FAILED": {"MAPPING_REQUIRED", "VALIDATING"},
    "APPLY_FAILED": {"READY_TO_APPLY"},
    "APPLIED": set(),
    "APPLIED_WITH_EXCLUSIONS": set(),
}


class HardwareTreeImportContractError(RuntimeError):
    """Stable contract error with a machine-readable code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _text(value: Any) -> str:
    return str(value or "").strip()


def validate_tree_type(tree_type: str) -> str:
    normalized = _text(tree_type).upper()
    if normalized not in TREE_TYPES:
        raise HardwareTreeImportContractError("TREE_TYPE_INVALID")
    return normalized


def validate_source_filename(filename: str) -> str:
    value = _text(filename)
    if not value:
        raise HardwareTreeImportContractError("SOURCE_FILENAME_REQUIRED")
    # Product history may keep a filename, never a company-local absolute path.
    if "/" in value or "\\" in value or PurePath(value).name != value:
        raise HardwareTreeImportContractError("SOURCE_PATH_NOT_ALLOWED")
    return value


def validate_source_sha256(value: str) -> str:
    digest = _text(value).lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise HardwareTreeImportContractError("SOURCE_SHA256_INVALID")
    return digest


def validate_mapping_profile(profile: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise HardwareTreeImportContractError("MAPPING_PROFILE_INVALID")
    sheet_name = _text(profile.get("sheet_name"))
    header_row = profile.get("header_row")
    path_columns = profile.get("path_columns")
    if not sheet_name:
        raise HardwareTreeImportContractError("SHEET_REQUIRED")
    if not isinstance(header_row, int) or header_row < 1:
        raise HardwareTreeImportContractError("HEADER_ROW_INVALID")
    if (
        not isinstance(path_columns, list)
        or not path_columns
        or any(not _text(item) for item in path_columns)
    ):
        raise HardwareTreeImportContractError("PATH_COLUMNS_REQUIRED")
    normalized = dict(profile)
    normalized["sheet_name"] = sheet_name
    normalized["header_row"] = header_row
    normalized["path_columns"] = [_text(item) for item in path_columns]
    metadata_columns = profile.get("metadata_columns") or []
    if not isinstance(metadata_columns, list):
        raise HardwareTreeImportContractError("METADATA_COLUMNS_INVALID")
    normalized["metadata_columns"] = [_text(item) for item in metadata_columns if _text(item)]
    business_key_column = _text(profile.get("business_key_column"))
    normalized["business_key_column"] = business_key_column or None
    return normalized


def validate_change_item(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise HardwareTreeImportContractError("CHANGE_INVALID")
    change_type = _text(item.get("change_type")).upper()
    decision = _text(item.get("decision") or "PENDING").upper()
    if change_type not in CHANGE_TYPES:
        raise HardwareTreeImportContractError("CHANGE_TYPE_INVALID")
    if decision not in CHANGE_DECISIONS:
        raise HardwareTreeImportContractError("CHANGE_DECISION_INVALID")
    normalized = dict(item)
    normalized["change_type"] = change_type
    normalized["decision"] = decision
    node_id = _text(item.get("node_id"))
    normalized["node_id"] = node_id or None
    normalized["business_key"] = _text(item.get("business_key")) or None
    if change_type in MUTATING_CHANGE_TYPES and not node_id:
        raise HardwareTreeImportContractError("CHANGE_NODE_ID_REQUIRED")
    if change_type in {"ADD", "UPDATE", "RENAME", "MOVE"}:
        after = item.get("after")
        if not isinstance(after, dict):
            raise HardwareTreeImportContractError("CHANGE_AFTER_REQUIRED")
    if change_type == "CONFLICT" and decision == "RESOLVED":
        after = item.get("after")
        if not isinstance(after, dict):
            raise HardwareTreeImportContractError("RESOLVED_CHANGE_AFTER_REQUIRED")
    return normalized


def validate_status_transition(current: str, target: str) -> None:
    if current not in IMPORT_STATUSES or target not in IMPORT_STATUSES:
        raise HardwareTreeImportContractError("IMPORT_STATUS_INVALID")
    if target not in STATUS_TRANSITIONS[current]:
        raise HardwareTreeImportContractError("IMPORT_STATUS_TRANSITION_INVALID")


def version_id_for(tree_type: str, sequence: int) -> str:
    normalized = validate_tree_type(tree_type)
    if sequence < 1:
        raise HardwareTreeImportContractError("VERSION_SEQUENCE_INVALID")
    prefix = "C" if normalized == "CIRCUIT_FEATURE" else "M"
    return f"{prefix}-{sequence:03d}"
