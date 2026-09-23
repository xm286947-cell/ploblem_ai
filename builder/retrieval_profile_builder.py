from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

import yaml


GROUPS = ("filter", "high_weight", "medium_weight", "low_weight")


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("retrieval_profile.yaml根节点必须为对象")
    return data


def _get_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _scalar_value(value: Any) -> Any:
    """Convert wrapped evidence/value objects to schema-safe scalar values.

    Retrieval Profile values are contractually limited to string/number/boolean.
    Standard Query fields may contain nested evidence wrappers such as
    {"effective": {"value": "...", ...}} or {"value": "...", ...}.
    """
    current = value
    visited: set[int] = set()
    while isinstance(current, dict):
        marker = id(current)
        if marker in visited:
            return ""
        visited.add(marker)
        next_value = None
        for key in ("value", "effective", "normalized", "original"):
            if key in current and current.get(key) not in (None, ""):
                next_value = current.get(key)
                break
        if next_value is None:
            return ""
        current = next_value
    if isinstance(current, (str, int, float, bool)):
        return current.strip() if isinstance(current, str) else current
    return ""


def _values(value: Any) -> list[Any]:
    items = value if isinstance(value, list) else [value]
    result: list[Any] = []
    for item in items:
        if isinstance(item, list):
            candidates = item
        else:
            candidates = [item]
        for candidate in candidates:
            scalar = _scalar_value(candidate)
            if scalar not in (None, "") and scalar not in result:
                result.append(scalar)
    return result


def _merge_groups(defaults: dict[str, Any], override: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result = deepcopy(defaults)
    for group in GROUPS:
        if group not in override:
            continue
        existing = {item["field"]: item for item in result.get(group, [])}
        for item in override.get(group, []) or []:
            existing[item["field"]] = item
        result[group] = list(existing.values())
    return result


class RetrievalProfileBuilder:
    VERSION = "M7.3-P1"
    SCHEMA_VERSION = "1.0"

    def __init__(self, root: str | Path, config_path: str | Path | None = None) -> None:
        self.root = Path(root).resolve()
        self.config_path = Path(config_path) if config_path else self.root / "config/retrieval_profile.yaml"
        self.config = _load_yaml(self.config_path)
        if not self.config.get("defaults", {}).get("groups"):
            raise ValueError("retrieval_profile.yaml缺少defaults.groups")

    def _field_entry(self, standard_query: dict[str, Any], spec: dict[str, Any], group: str) -> dict[str, Any]:
        field_path = str(spec["field"])
        raw = _get_path(standard_query, field_path)
        source = "DIRECT"
        confidence = 1.0
        evidence_type = ""
        value: Any = raw
        if isinstance(raw, dict) and "effective" in raw:
            value = raw.get("effective")
            source = str(raw.get("effective_source") or "EMPTY")
            confidence = float(raw.get("confidence", 1.0 if source in {"ORIGINAL", "NORMALIZED"} else 0.0))
            evidence_type = str(raw.get("evidence_type") or "")
        vals = _values(value)
        completeness = 1.0 if vals else 0.0
        defaults = self.config.get("defaults", {})
        source_factor = float(defaults.get(f"{source.lower()}_source_factor", 1.0))
        if source == "INFERRED":
            confidence = max(float(defaults.get("confidence_floor", 0.35)), confidence)
            source_factor *= float(defaults.get("inferred_confidence_factor", 0.85))
        elif source == "EMPTY":
            confidence = 0.0
            source_factor = 0.0
        base_weight = float(spec.get("weight", 0.0))
        effective_weight = base_weight * source_factor * confidence * completeness
        entry = {
            "field": field_path,
            "values": vals,
            "source": source,
            "confidence": round(confidence, 6),
            "completeness": completeness,
            "base_weight": round(base_weight, 6),
            "effective_weight": round(effective_weight, 6),
            "evidence_type": evidence_type,
        }
        if group == "filter":
            entry["filter_mode"] = str(spec.get("mode", "SOFT")).upper()
        return entry

    def build(self, standard_query: dict[str, Any], source_path: str = "") -> dict[str, Any]:
        query_id = str(_get_path(standard_query, "metadata.query_id") or _get_path(standard_query, "problem.query_id.effective") or "")
        if not query_id:
            raise ValueError("Standard Query缺少query_id")
        product = str(_get_path(standard_query, "organization.product.effective") or "")
        defaults = self.config["defaults"]
        product_override = (self.config.get("products") or {}).get(product, {})
        groups = _merge_groups(defaults["groups"], product_override.get("groups", {}))

        result_groups: dict[str, list[dict[str, Any]]] = {}
        warnings: list[dict[str, Any]] = []
        total_fields = 0
        available_fields = 0
        total_weight = 0.0
        available_weight = 0.0
        for group in GROUPS:
            entries = []
            for spec in groups.get(group, []):
                total_fields += 1
                entry = self._field_entry(standard_query, spec, group)
                total_weight += entry["base_weight"]
                if entry["values"]:
                    available_fields += 1
                    available_weight += entry["base_weight"]
                else:
                    warnings.append({"code": "PROFILE_FIELD_EMPTY", "field": entry["field"], "group": group})
                entries.append(entry)
            result_groups[group] = entries

        coverage = available_fields / total_fields if total_fields else 0.0
        weighted_coverage = available_weight / total_weight if total_weight else 0.0
        channel_fields = {
            "keyword": [e["field"] for g in ("high_weight", "medium_weight", "low_weight") for e in result_groups[g] if e["values"]],
            "bm25": [e["field"] for g in ("high_weight", "medium_weight") for e in result_groups[g] if e["values"]],
            "embedding": [e["field"] for g in ("high_weight", "medium_weight") for e in result_groups[g] if e["values"]],
            "llm_rerank": [e["field"] for g in ("high_weight", "medium_weight", "low_weight") for e in result_groups[g] if e["values"]],
        }
        build_status = "SUCCESS" if coverage >= 0.5 else "PARTIAL_SUCCESS"
        return {
            "metadata": {
                "source_standard_query": source_path,
                "profile_builder_version": self.VERSION,
                "profile_schema_version": self.SCHEMA_VERSION,
                "profile_config_version": str(self.config.get("version", "")),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            "query_id": query_id,
            "product_profile": product if product in (self.config.get("products") or {}) else "DEFAULT",
            **result_groups,
            "channels": channel_fields,
            "profile_quality": {
                "available_fields": available_fields,
                "configured_fields": total_fields,
                "coverage": round(coverage, 6),
                "weighted_coverage": round(weighted_coverage, 6),
            },
            "warnings": warnings,
            "lineage": {**{k: str(v) for k, v in (standard_query.get("lineage") or {}).items()}, "retrieval_profile_builder_version": self.VERSION, "retrieval_profile_config_version": str(self.config.get("version", ""))},
            "build_status": build_status,
        }

    def build_file(self, standard_query_path: str | Path) -> dict[str, Any]:
        path = Path(standard_query_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        try:
            source = str(path.relative_to(self.root))
        except ValueError:
            source = str(path)
        return self.build(data, source)
