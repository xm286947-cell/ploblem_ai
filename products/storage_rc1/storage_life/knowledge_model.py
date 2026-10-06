"""Executable Storage Lifetime knowledge-production model.

This module does not define a second knowledge system.  It composes the
existing Storage specification templates, parameter engineering semantics and
FormulaRegistry into one deterministic, scenario-consumable model.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .lifetime_engine import FormulaRegistry


class StorageLifetimeKnowledgeModelError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise StorageLifetimeKnowledgeModelError(
            "KNOWLEDGE_MODEL_SOURCE_MISSING", str(path)
        )
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise StorageLifetimeKnowledgeModelError(
            "KNOWLEDGE_MODEL_SOURCE_INVALID", str(path)
        )
    return payload


@dataclass(frozen=True)
class StorageLifetimeKnowledgeModel:
    model: dict[str, Any]
    parameter_knowledge: dict[str, Any]
    spec_templates: dict[str, Any]

    @classmethod
    def from_product_root(
        cls, product_root: str | Path | None = None
    ) -> "StorageLifetimeKnowledgeModel":
        root = (
            Path(product_root).resolve()
            if product_root is not None
            else Path(__file__).resolve().parents[1]
        )
        instance = cls(
            model=_load_yaml(
                root / "config" / "storage_lifetime_knowledge_model.yaml"
            ),
            parameter_knowledge=_load_yaml(
                root / "config" / "parameter_knowledge.yaml"
            ),
            spec_templates=_load_yaml(
                root / "config" / "spec_templates.yaml"
            ),
        )
        instance.validate()
        return instance

    @property
    def canonical_device_types(self) -> tuple[str, ...]:
        values = self.model.get("canonical_device_types") or []
        return tuple(str(value) for value in values)

    @property
    def primary_focus(self) -> str:
        return str(self.model.get("primary_focus") or "")

    def canonicalize_device_type(self, value: str) -> str:
        raw = str(value or "").strip()
        if raw in self.canonical_device_types:
            return raw
        alias_map = {
            str(alias).strip().casefold(): str(target).strip()
            for alias, target in (
                self.model.get("compatibility_aliases") or {}
            ).items()
        }
        canonical_casefold = {
            item.casefold(): item for item in self.canonical_device_types
        }
        resolved = alias_map.get(raw.casefold()) or canonical_casefold.get(
            raw.casefold()
        )
        if not resolved:
            raise StorageLifetimeKnowledgeModelError(
                "DEVICE_TYPE_UNSUPPORTED", raw
            )
        return resolved

    def device_profile(self, device_type: str) -> dict[str, Any]:
        canonical = self.canonicalize_device_type(device_type)
        spec = (
            self.spec_templates.get("device_types", {}).get(canonical) or {}
        )
        knowledge = (
            self.parameter_knowledge.get("device_types", {}).get(canonical)
            or {}
        )
        parameter_semantics = knowledge.get("parameters") or {}
        role_map = self.model.get("role_to_knowledge") or {}
        semantic_classes = self.model.get("semantic_classes") or {}

        parameters: list[dict[str, Any]] = []
        for name in spec.get("analysis_fields") or []:
            name = str(name)
            semantic = parameter_semantics.get(name)
            role = (
                str(semantic.get("role"))
                if isinstance(semantic, dict) and semantic.get("role")
                else None
            )
            requirements = (
                list(role_map.get(role) or [])
                if role
                else ["PARAMETER_DEFINITION"]
            )
            consumers: list[str] = []
            for requirement in requirements:
                for consumer in (
                    semantic_classes.get(requirement, {}).get("consumers")
                    or []
                ):
                    if consumer not in consumers:
                        consumers.append(str(consumer))
            parameters.append(
                {
                    "canonical_name": name,
                    "display_name": (
                        spec.get("fields", {}).get(name) or name
                    ),
                    "role": role,
                    "role_source": (
                        "parameter_knowledge.yaml"
                        if role
                        else "SPEC_TEMPLATE_ONLY"
                    ),
                    "knowledge_requirements": requirements,
                    "scenario_consumers": consumers,
                    "knowledge_gap": (
                        None
                        if role
                        else "PARAMETER_ROLE_UNCLASSIFIED"
                    ),
                }
            )

        formulas = []
        for formula_id in (
            self.model.get("formula_bindings", {}).get(canonical) or []
        ):
            spec_item = FormulaRegistry.describe(str(formula_id))
            formulas.append(
                {
                    "formula_id": spec_item.formula_id,
                    "formula_version": spec_item.formula_version,
                    "description": spec_item.description,
                    "required_inputs": list(spec_item.required_inputs),
                    "required_knowledge_parameters": list(
                        spec_item.required_knowledge_parameters
                    ),
                    "protocol_semantics": bool(
                        spec_item.protocol_semantics
                    ),
                }
            )

        return {
            "schema_version": self.model.get("version"),
            "device_type": canonical,
            "primary_focus": canonical == self.primary_focus,
            "operation_model": knowledge.get("operation_model"),
            "parameters": parameters,
            "formulas": formulas,
        }

    def snapshot(self) -> dict[str, Any]:
        profiles = [
            self.device_profile(device_type)
            for device_type in self.canonical_device_types
        ]
        gaps = [
            {
                "device_type": profile["device_type"],
                "canonical_name": parameter["canonical_name"],
                "gap": parameter["knowledge_gap"],
            }
            for profile in profiles
            for parameter in profile["parameters"]
            if parameter["knowledge_gap"]
        ]
        return {
            "schema_version": self.model.get("version"),
            "primary_focus": self.primary_focus,
            "canonical_device_types": list(self.canonical_device_types),
            "profiles": profiles,
            "knowledge_gaps": gaps,
            "policy": {
                "formula_policy": self.model.get("formula_policy") or {},
                "production_policy": (
                    self.model.get("production_policy") or {}
                ),
                "metadata_contract": (
                    self.model.get("metadata_contract") or {}
                ),
            },
        }

    def validate(self) -> None:
        if self.model.get("version") != "storage-lifetime-knowledge/v1":
            raise StorageLifetimeKnowledgeModelError(
                "KNOWLEDGE_MODEL_VERSION_INVALID"
            )
        canonical = self.canonical_device_types
        if not canonical or len(set(canonical)) != len(canonical):
            raise StorageLifetimeKnowledgeModelError(
                "CANONICAL_DEVICE_TYPES_INVALID"
            )
        if "Raw NAND" in canonical or "Raw Flash" in canonical:
            raise StorageLifetimeKnowledgeModelError(
                "RAW_NAND_BUSINESS_TYPE_FORBIDDEN"
            )
        if self.primary_focus != "NAND Flash":
            raise StorageLifetimeKnowledgeModelError(
                "PRIMARY_FOCUS_INVALID", self.primary_focus
            )

        parameter_types = set(
            (self.parameter_knowledge.get("device_types") or {}).keys()
        )
        template_types = set(
            (self.spec_templates.get("device_types") or {}).keys()
        )
        if set(canonical) != parameter_types:
            raise StorageLifetimeKnowledgeModelError(
                "PARAMETER_KNOWLEDGE_DEVICE_TYPE_DRIFT",
                f"{sorted(parameter_types)}",
            )
        if set(canonical) != template_types:
            raise StorageLifetimeKnowledgeModelError(
                "SPEC_TEMPLATE_DEVICE_TYPE_DRIFT",
                f"{sorted(template_types)}",
            )

        semantic_classes = self.model.get("semantic_classes") or {}
        if not semantic_classes:
            raise StorageLifetimeKnowledgeModelError(
                "SEMANTIC_CLASSES_MISSING"
            )
        role_map = self.model.get("role_to_knowledge") or {}
        known_roles = {
            str(item.get("role"))
            for device in (
                self.parameter_knowledge.get("device_types") or {}
            ).values()
            for item in (device.get("parameters") or {}).values()
            if isinstance(item, dict) and item.get("role")
        }
        missing_roles = sorted(known_roles - set(role_map))
        if missing_roles:
            raise StorageLifetimeKnowledgeModelError(
                "PARAMETER_ROLE_MAPPING_MISSING",
                ",".join(missing_roles),
            )
        for role, classes in role_map.items():
            unknown = [
                str(value)
                for value in (classes or [])
                if value not in semantic_classes
            ]
            if unknown:
                raise StorageLifetimeKnowledgeModelError(
                    "SEMANTIC_CLASS_UNKNOWN",
                    f"{role}:{','.join(unknown)}",
                )

        aliases = self.model.get("compatibility_aliases") or {}
        for alias, target in aliases.items():
            if str(target) not in canonical:
                raise StorageLifetimeKnowledgeModelError(
                    "DEVICE_ALIAS_TARGET_INVALID",
                    f"{alias}->{target}",
                )

        for device_type in canonical:
            spec = self.spec_templates["device_types"][device_type]
            fields = set((spec.get("fields") or {}).keys())
            analysis_fields = [
                str(value) for value in (spec.get("analysis_fields") or [])
            ]
            missing_fields = sorted(set(analysis_fields) - fields)
            if missing_fields:
                raise StorageLifetimeKnowledgeModelError(
                    "ANALYSIS_FIELD_UNDECLARED",
                    f"{device_type}:{','.join(missing_fields)}",
                )
            parameter_defs = (
                self.parameter_knowledge["device_types"][device_type].get(
                    "parameters"
                )
                or {}
            )
            undeclared_parameter_semantics = sorted(
                set(parameter_defs) - fields
            )
            if undeclared_parameter_semantics:
                raise StorageLifetimeKnowledgeModelError(
                    "PARAMETER_SEMANTICS_FIELD_UNDECLARED",
                    (
                        f"{device_type}:"
                        + ",".join(undeclared_parameter_semantics)
                    ),
                )

        registered = set(FormulaRegistry.SPECS)
        for device_type, formula_ids in (
            self.model.get("formula_bindings") or {}
        ).items():
            if device_type not in canonical:
                raise StorageLifetimeKnowledgeModelError(
                    "FORMULA_DEVICE_TYPE_INVALID", str(device_type)
                )
            unknown = [
                str(value)
                for value in (formula_ids or [])
                if value not in registered
            ]
            if unknown:
                raise StorageLifetimeKnowledgeModelError(
                    "FORMULA_NOT_REGISTERED",
                    f"{device_type}:{','.join(unknown)}",
                )
