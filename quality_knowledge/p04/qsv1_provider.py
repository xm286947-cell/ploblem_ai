"""Read-only P04 provider backed by published QualityScenario V1 records.

This adapter is production-data only. It projects the frozen QSV1 production
provenance into the existing P04 consumer contract; it never seeds records and
never writes legacy quality_scenario* tables.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from quality_knowledge.p04.adapter import ProviderSnapshot
from quality_knowledge.p04.contracts import P04State
from quality_knowledge.quality_scenario_v1 import ScenarioStatus
from quality_knowledge.quality_scenario_v1_store import SQLiteQualityScenarioV1Repository
from quality_knowledge.scenario_source_bundle_v1 import ScenarioSourceBundleV1SnapshotStore


class QualityScenarioV1P04Provider:
    def __init__(self, db_path: str | Path):
        path = Path(db_path)
        self.db_path = path.resolve()
        self.repository = SQLiteQualityScenarioV1Repository(self.db_path)
        self.bundle_snapshots = ScenarioSourceBundleV1SnapshotStore(self.db_path)

    def _bundle_context(self, scenario: Any) -> dict[str, Any]:
        for evidence in scenario.evidence_refs:
            if evidence.evidence_type != "QSV1_PRODUCTION_PROVENANCE":
                continue
            locator = str(evidence.content_ref or "").strip()
            parsed = urlparse(locator)
            if parsed.scheme != "scenario-source-bundle":
                continue
            bundle_id = unquote(parsed.netloc or "")
            revision = unquote(parsed.path.lstrip("/"))
            if not bundle_id or not revision:
                continue
            bundle = self.bundle_snapshots.get(bundle_id, revision)
            if not isinstance(bundle, dict):
                continue
            selected = bundle.get("selected_issue") or {}
            facts = bundle.get("facts") or {}
            if not isinstance(selected, dict):
                selected = {}
            if not isinstance(facts, dict):
                facts = {}
            source_problem_id = str(
                selected.get("knowledge_id")
                or selected.get("business_issue_id")
                or ""
            ).strip()
            return {
                "product": str(
                    selected.get("product_model")
                    or facts.get("product_model")
                    or scenario.product_name
                    or scenario.product_code
                    or ""
                ).strip(),
                "customer": str(
                    selected.get("customer")
                    or facts.get("customer")
                    or ""
                ).strip(),
                "industry": str(
                    selected.get("industry")
                    or facts.get("industry")
                    or ""
                ).strip(),
                "ipmt": str(selected.get("ipmt") or facts.get("ipmt") or "").strip(),
                "spdt": str(selected.get("spdt") or facts.get("spdt") or "").strip(),
                "source_problem_ids": [source_problem_id] if source_problem_id else [],
                "source_contract_version": "scenario-source-bundle/v1",
            }
        return {}

    def _public_record(self, scenario: Any) -> dict[str, Any]:
        payload = scenario.model_dump(mode="json")
        sources = [item.model_dump(mode="json") for item in scenario.source_problem_refs]
        evidence = [item.model_dump(mode="json") for item in scenario.evidence_refs]
        context = self._bundle_context(scenario)

        if not context.get("source_problem_ids"):
            fallback = next(
                (
                    str(item.source_id or item.canonical_itr or "").strip()
                    for item in scenario.source_problem_refs
                    if item.source_type in {"SELECTED_ISSUE", "ITR"}
                    and str(item.source_id or item.canonical_itr or "").strip()
                ),
                "",
            )
            context["source_problem_ids"] = [fallback] if fallback else []

        product = str(
            context.get("product")
            or scenario.product_name
            or scenario.product_code
            or ""
        ).strip()
        customer = str(context.get("customer") or "").strip()
        industry = str(context.get("industry") or "").strip()
        quality_focus = str(
            scenario.quality_concern_name or scenario.quality_concern_code or ""
        ).strip()
        lifecycle = str(
            scenario.lifecycle_stage_name or scenario.lifecycle_stage_code or ""
        ).strip()
        business_activity = str(
            scenario.business_activity_name or scenario.business_activity_code or ""
        ).strip()

        return {
            "scenario_id": scenario.scenario_id,
            "scenario_name": scenario.scenario_name,
            "status": scenario.status.value if hasattr(scenario.status, "value") else str(scenario.status),
            "product": product,
            "product_ref": product,
            "product_code": scenario.product_code,
            "product_name": scenario.product_name,
            "customer": customer,
            "customer_ref": customer,
            "industry": industry,
            "industry_ref": industry,
            "ipmt": str(context.get("ipmt") or ""),
            "spdt": str(context.get("spdt") or ""),
            "lifecycle": lifecycle,
            "lifecycle_stage_code": scenario.lifecycle_stage_code,
            "lifecycle_stage_name": scenario.lifecycle_stage_name,
            "business_activity": business_activity,
            "business_activity_code": scenario.business_activity_code,
            "business_activity_name": scenario.business_activity_name,
            "quality_focus": quality_focus,
            "quality_concern_code": scenario.quality_concern_code,
            "quality_concern_name": scenario.quality_concern_name,
            "scenario_description": scenario.scenario_description,
            "trigger_summary": scenario.trigger_condition,
            "trigger_condition": scenario.trigger_condition,
            "failure_mode_summary": scenario.scenario_description,
            "expected_result": scenario.expected_result,
            "applicability_scope": scenario.applicability_scope,
            "source_problem_ids": list(context.get("source_problem_ids") or []),
            "source_refs": sources,
            "evidence_refs": evidence,
            "source_contract_version": context.get("source_contract_version"),
            "relation_status": (
                "OBSERVED_IN_PROBLEM_EVIDENCE"
                if context.get("source_problem_ids")
                else "NO_RELATION_MAPPING"
            ),
            "scenario_version": scenario.scenario_version,
            "updated_at": scenario.version.updated_at,
            "published_at": scenario.version.published_at,
            "contract_source": "quality-scenario-v1",
            "_qsv1": payload,
        }

    def snapshot(self) -> ProviderSnapshot:
        scenarios = self.repository.list(status=ScenarioStatus.PUBLISHED)
        records = tuple(self._public_record(item) for item in scenarios)
        revision_material = json.dumps(
            records,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        revision = "qsv1-" + hashlib.sha256(revision_material.encode("utf-8")).hexdigest()[:16]
        return ProviderSnapshot(
            scenarios=records,
            result_revision=revision,
            state=P04State.NORMAL if records else P04State.EMPTY,
            warnings=(),
        )

    def diagnostic(self) -> dict[str, Any]:
        candidates = self.repository.list(status=ScenarioStatus.CANDIDATE)
        published = self.repository.list(status=ScenarioStatus.PUBLISHED)
        return {
            "V1_DB_ABSOLUTE_PATH": str(self.db_path),
            "V1_CANDIDATE_COUNT": len(candidates),
            "V1_PUBLISHED_COUNT": len(published),
            "P04_PROVIDER_TYPE": type(self).__name__,
            "P04_PUBLISHED_COUNT": len(published),
            "SYNTHETIC_FIXTURE_USED": "NO",
        }


class QualityScenarioV1PortraitProvider:
    """Portrait port backed by the same enriched QSV1/P04 snapshot."""

    def __init__(self, provider: QualityScenarioV1P04Provider) -> None:
        self.provider = provider

    def query(self, filters: dict[str, str]) -> tuple[list[dict[str, Any]], str]:
        snapshot = self.provider.snapshot()
        if snapshot.state in {
            P04State.DATA_UNAVAILABLE,
            P04State.ERROR,
            P04State.PERMISSION_UNAVAILABLE,
        }:
            raise RuntimeError("QSV1_P04_PROVIDER_UNAVAILABLE")
        rows = [
            dict(row)
            for row in snapshot.scenarios
            if all(str(row.get(key) or "") == value for key, value in filters.items())
        ]
        return rows, snapshot.result_revision


__all__ = [
    "QualityScenarioV1P04Provider",
    "QualityScenarioV1PortraitProvider",
]
