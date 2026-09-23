from __future__ import annotations

from typing import Any, Type

from quality_knowledge.config_loader import normalize_header
from quality_knowledge.models.issue import (
    IssueFact, ProductContext, OccurrenceFact, EscapeFact, SolutionFact,
    VerificationFact,
)
from .base import BaseIssueAdapter, clean


def _lookup(row: dict[str, Any], aliases: list[str]) -> str:
    for alias in aliases:
        key = normalize_header(alias)
        if key in row and clean(row[key]):
            return clean(row[key])
    return ""


def _section_values(row: dict[str, Any], cfg: dict[str, Any], section: str) -> tuple[dict[str, str], dict[str, str]]:
    values: dict[str, str] = {}
    matched: dict[str, str] = {}
    for target, spec in (cfg.get(section) or {}).items():
        if not isinstance(spec, dict):
            continue
        aliases = list(spec.get("aliases") or [])
        value = _lookup(row, aliases)
        if value:
            values[target] = value
            matched[f"{section}.{target}"] = value
    return values, matched


def _model_kwargs(model_cls: Type, values: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    fields = set(model_cls.model_fields)
    known = {k: v for k, v in values.items() if k in fields}
    extra = {k: v for k, v in values.items() if k not in fields}
    return known, extra


class ConfigurableIssueAdapter(BaseIssueAdapter):
    def __init__(self, mapping_config=None):
        self.mapping_config = mapping_config

    """YAML-driven field adapter.

    Stable DTO fields are written into their typed section; configured fields that do
    not yet have a typed DTO slot are preserved in product_extension.configured_fields.
    Raw headers/values remain untouched in raw_record.
    """

    def build(self, r, i, k, h, f, s, n, b):
        if self.mapping_config is None:
            raise RuntimeError("MAPPING_NOT_INITIALIZED: runtime mapping must come from ACTIVE DB configuration")
        from quality_knowledge.mapping.runtime import effective_to_adapter_config
        cfg = effective_to_adapter_config(self.mapping_config)
        configured_fields: dict[str, str] = {}
        leftovers: dict[str, str] = {}

        fv, m = _section_values(r, cfg, "issue_fact"); configured_fields.update(m)
        fknown, fextra = _model_kwargs(IssueFact, fv); leftovers.update({f"issue_fact.{x}":v for x,v in fextra.items()})
        fact = IssueFact(**fknown)

        cv, m = _section_values(r, cfg, "product_context"); configured_fields.update(m)
        cknown, cextra = _model_kwargs(ProductContext, cv); leftovers.update({f"product_context.{x}":v for x,v in cextra.items()})
        # mirror common fact fields when context config omits them
        cknown.setdefault("product", fact.product); cknown.setdefault("platform", fact.platform); cknown.setdefault("module", fact.module); cknown.setdefault("business_group", fact.business_group)
        context = ProductContext(**cknown)

        ov, m = _section_values(r, cfg, "occurrence"); configured_fields.update(m)
        # root_cause_evidence is preserved as extra; typed source is original_reason/root_cause_original
        if ov.get("original_reason"):
            ov.setdefault("root_cause_original", ov["original_reason"])
        oknown, oextra = _model_kwargs(OccurrenceFact, ov); leftovers.update({f"occurrence.{x}":v for x,v in oextra.items()})
        occurrence = OccurrenceFact(**oknown)

        ev, m = _section_values(r, cfg, "escape"); configured_fields.update(m)
        if ev.get("original_reason"):
            ev.setdefault("root_cause_original", ev["original_reason"])
        eknown, eextra = _model_kwargs(EscapeFact, ev); leftovers.update({f"escape.{x}":v for x,v in eextra.items()})
        escape = EscapeFact(**eknown)

        sv, m = _section_values(r, cfg, "solution"); configured_fields.update(m)
        # configured compatibility aliases with current DTO
        if sv.get("dev_action") and not sv.get("technical_action"):
            sv["technical_action"] = sv["dev_action"]
        if sv.get("escape_improvement_action") and not sv.get("reusable_action"):
            sv["reusable_action"] = sv["escape_improvement_action"]
        sknown, sextra = _model_kwargs(SolutionFact, sv); leftovers.update({f"solution.{x}":v for x,v in sextra.items()})
        solution = SolutionFact(**sknown)

        vv, m = _section_values(r, cfg, "verification"); configured_fields.update(m)
        vknown, vextra = _model_kwargs(VerificationFact, vv); leftovers.update({f"verification.{x}":v for x,v in vextra.items()})
        verification = VerificationFact(**vknown)

        extension, m = _section_values(r, cfg, "extension"); configured_fields.update(m)
        recurrence, m = _section_values(r, cfg, "recurrence"); configured_fields.update(m)
        if recurrence:
            extension["recurrence"] = recurrence
        if leftovers:
            extension["typed_model_pending"] = leftovers
        extension["configured_fields"] = configured_fields

        return self.common(
            r, i, k, h, f, s, n, b,
            fact=fact, context=context, occurrence=occurrence, escape=escape,
            solution=solution, verification=verification, extension=extension,
        )
