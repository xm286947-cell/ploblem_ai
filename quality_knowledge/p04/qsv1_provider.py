"""Read-only P04 provider backed by published QualityScenario V1 records.

This adapter is intentionally production-data only. It never seeds records and
never touches legacy quality_scenario* tables.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from quality_knowledge.p04.adapter import ProviderSnapshot
from quality_knowledge.p04.contracts import P04State
from quality_knowledge.quality_scenario_v1 import ScenarioStatus
from quality_knowledge.quality_scenario_v1_store import SQLiteQualityScenarioV1Repository


class QualityScenarioV1P04Provider:
    def __init__(self, db_path: str | Path):
        path = Path(db_path)
        self.db_path = path.resolve()
        self.repository = SQLiteQualityScenarioV1Repository(self.db_path)

    @staticmethod
    def _public_record(scenario: Any) -> dict[str, Any]:
        payload = scenario.model_dump(mode="json")
        sources = [item.model_dump(mode="json") for item in scenario.source_problem_refs]
        evidence = [item.model_dump(mode="json") for item in scenario.evidence_refs]
        return {
            "scenario_id": scenario.scenario_id,
            "scenario_name": scenario.scenario_name,
            "status": str(scenario.status),
            "product": scenario.product_name or scenario.product_code,
            "product_code": scenario.product_code,
            "product_name": scenario.product_name,
            "lifecycle_stage_code": scenario.lifecycle_stage_code,
            "lifecycle_stage_name": scenario.lifecycle_stage_name,
            "business_activity_code": scenario.business_activity_code,
            "business_activity_name": scenario.business_activity_name,
            "quality_concern_code": scenario.quality_concern_code,
            "quality_concern_name": scenario.quality_concern_name,
            "scenario_description": scenario.scenario_description,
            "trigger_condition": scenario.trigger_condition,
            "expected_result": scenario.expected_result,
            "applicability_scope": scenario.applicability_scope,
            "source_refs": sources,
            "evidence_refs": evidence,
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


__all__ = ["QualityScenarioV1P04Provider"]
