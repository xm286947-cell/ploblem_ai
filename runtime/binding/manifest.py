"""Fail-closed validation for the Runtime four-domain binding manifest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from contracts.runtime_binding.v1 import RUNTIME_FOUR_DOMAIN_BINDING_VERSION


EXPECTED_RUNTIME_DOMAINS = (
    "STORAGE",
    "MAJOR_ISSUE",
    "REVERSE_QUALITY",
    "KNOWLEDGE",
)
_REQUIRED_DOMAIN_FIELDS = {
    "domain_id",
    "agent_ids",
    "config_paths",
    "adapter",
    "ownership",
    "status",
}


class RuntimeBindingError(ValueError):
    """Raised when a Runtime domain binding cannot be safely admitted."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _require_text(value: Any, code: str, detail: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeBindingError(code, detail)
    return value.strip()


def _require_string_list(value: Any, code: str, detail: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise RuntimeBindingError(code, detail)
    return [item.strip() for item in value]


def validate_runtime_binding(
    binding: Mapping[str, Any],
    *,
    repository_root: str | Path | None = None,
) -> Mapping[str, Any]:
    """Validate a binding and its referenced Agent YAML files.

    ``repository_root`` is optional for callers validating an embedded
    manifest. CI and local gates should provide it so paths and Agent IDs are
    checked against the checked-out source tree.
    """

    if not isinstance(binding, Mapping):
        raise RuntimeBindingError("RUNTIME_BINDING_INVALID", "manifest must be an object")
    if binding.get("binding_contract_version") != RUNTIME_FOUR_DOMAIN_BINDING_VERSION:
        raise RuntimeBindingError("RUNTIME_BINDING_VERSION_MISMATCH")
    if binding.get("runtime_contract_version") != "unified-agent-runtime/p0.3":
        raise RuntimeBindingError("RUNTIME_CONTRACT_VERSION_MISMATCH")
    if binding.get("agent_config_contract") != "AGENT-CONFIG-001":
        raise RuntimeBindingError("AGENT_CONFIG_CONTRACT_MISMATCH")
    if binding.get("status") != "BOUND":
        raise RuntimeBindingError("RUNTIME_BINDING_NOT_BOUND")

    domains = binding.get("domains")
    if not isinstance(domains, list):
        raise RuntimeBindingError("RUNTIME_DOMAIN_SET_MISMATCH", "domains must be a list")
    domain_ids = [item.get("domain_id") for item in domains if isinstance(item, Mapping)]
    if tuple(domain_ids) != EXPECTED_RUNTIME_DOMAINS:
        raise RuntimeBindingError(
            "RUNTIME_DOMAIN_SET_MISMATCH",
            f"expected {EXPECTED_RUNTIME_DOMAINS}, got {tuple(domain_ids)}",
        )

    seen_agents: set[str] = set()
    root = Path(repository_root).resolve() if repository_root is not None else None
    for domain in domains:
        if not isinstance(domain, Mapping) or not _REQUIRED_DOMAIN_FIELDS <= set(domain):
            raise RuntimeBindingError("RUNTIME_DOMAIN_BINDING_INVALID")
        if domain["status"] != "BOUND":
            raise RuntimeBindingError("RUNTIME_DOMAIN_NOT_BOUND", str(domain["domain_id"]))
        if domain["ownership"] != "RUNTIME_EXECUTION_ONLY":
            raise RuntimeBindingError(
                "RUNTIME_BINDING_OWNERSHIP_VIOLATION", str(domain["domain_id"])
            )
        agents = _require_string_list(
            domain["agent_ids"], "RUNTIME_DOMAIN_BINDING_INVALID", "agent_ids"
        )
        paths = _require_string_list(
            domain["config_paths"], "RUNTIME_DOMAIN_BINDING_INVALID", "config_paths"
        )
        _require_text(domain["adapter"], "RUNTIME_DOMAIN_BINDING_INVALID", "adapter")
        if len(agents) != len(paths):
            raise RuntimeBindingError(
                "RUNTIME_AGENT_CONFIG_CARDINALITY_MISMATCH", str(domain["domain_id"])
            )
        overlap = seen_agents.intersection(agents)
        if overlap:
            raise RuntimeBindingError("RUNTIME_AGENT_ID_DUPLICATE", sorted(overlap)[0])
        seen_agents.update(agents)
        if root is None:
            continue
        for agent_id, relative_path in zip(agents, paths):
            config_path = (root / relative_path).resolve()
            if root not in config_path.parents:
                raise RuntimeBindingError("RUNTIME_CONFIG_PATH_ESCAPE", relative_path)
            if not config_path.is_file():
                raise RuntimeBindingError("RUNTIME_AGENT_CONFIG_MISSING", relative_path)
            try:
                raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                raise RuntimeBindingError("RUNTIME_AGENT_CONFIG_INVALID", relative_path) from exc
            if not isinstance(raw, Mapping) or raw.get("agent_id") != agent_id:
                raise RuntimeBindingError("RUNTIME_AGENT_ID_MISMATCH", relative_path)

    boundary = binding.get("boundary")
    if not isinstance(boundary, Mapping):
        raise RuntimeBindingError("RUNTIME_BOUNDARY_INVALID")
    expected_false = ("direct_provider_calls", "domain_retry_loops", "secret_persistence")
    if any(boundary.get(key) is not False for key in expected_false):
        raise RuntimeBindingError("RUNTIME_BINDING_OWNERSHIP_VIOLATION", "boundary")
    for key in ("provider_execution", "retry_policy", "secret_resolution", "execution_snapshot"):
        if boundary.get(key) != "RUNTIME":
            raise RuntimeBindingError("RUNTIME_BINDING_OWNERSHIP_VIOLATION", key)
    if boundary.get("domain_semantics") != "DOMAIN":
        raise RuntimeBindingError("RUNTIME_BINDING_OWNERSHIP_VIOLATION", "domain_semantics")
    return binding


def load_runtime_binding(
    path: str | Path | None = None,
    *,
    repository_root: str | Path | None = None,
) -> Mapping[str, Any]:
    """Load and validate the checked-in four-domain binding."""

    manifest_path = Path(path) if path is not None else (
        Path(__file__).resolve().parents[2]
        / "contracts/runtime_binding/v1/runtime_binding.json"
    )
    try:
        binding = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeBindingError("RUNTIME_BINDING_INVALID", str(manifest_path)) from exc
    root = repository_root if repository_root is not None else manifest_path.parents[3]
    return validate_runtime_binding(binding, repository_root=root)


__all__ = [
    "EXPECTED_RUNTIME_DOMAINS",
    "RuntimeBindingError",
    "load_runtime_binding",
    "validate_runtime_binding",
]
