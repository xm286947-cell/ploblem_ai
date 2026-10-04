"""Immutable, auditable source bundle snapshots for QS Fast MVP Wave 1.

The bundle is a read-only projection over existing source_material records.
It is not a new business master and does not run Reverse Quality.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from quality_knowledge.materials import normalize_itr


CONTRACT_VERSION = "scenario-source-bundle/v1"
SOURCE_TYPES = ("SOFTWARE_ASSESSMENT", "RESOLUTION", "ITR", "MISSED_TEST")
SOURCE_MATERIAL_TYPES = {
    "SOFTWARE_ASSESSMENT": "SOFTWARE_OPERATION",
    "RESOLUTION": "ITR_CS",
    "ITR": "ITR_SOURCE",
    "MISSED_TEST": "ESCAPE_ANALYSIS",
}

# Authority is field-scoped: supporting records can enrich but cannot replace
# software-assessment identity/scope or fabricate a missing conclusion.
FIELD_AUTHORITIES = {
    "business_key": "SOFTWARE_ASSESSMENT",
    "assessment": "SOFTWARE_ASSESSMENT",
    "problem_description": "SOFTWARE_ASSESSMENT",
    "resolution": "RESOLUTION",
    "itr_context": "ITR",
    "missed_test": "MISSED_TEST",
}

_ASSESSMENT_FIELDS = (
    "业务键", "问题描述", "问题信息_问题描述", "问题现象", "问题信息_问题现象",
    "考核信息_考核状态", "流程信息_考核状态", "考核状态", "考核信息_考核结果",
    "考核信息_考核结论", "考核结果", "考核结论", "考核意见", "责任信息_责任人",
    "责任人", "责任信息_责任部门", "责任部门", "考核信息_考核分", "考核分",
    "产品型号", "问题信息_产品型号", "客户名称", "问题信息_客户名称",
)
_RESOLUTION_FIELDS = (
    "问题信息_问题原因定位", "问题原因定位", "技术根因分析与纠正_TRC根因",
    "技术根因分析与纠正_TRC纠正信息", "问题处理结果_问题解决方案", "问题解决方案",
    "恢复措施", "恢复措施执行_现场作业记录",
)
_ITR_FIELDS = (
    "问题信息_ITR单号", "ITR单号", "问题信息_问题描述", "问题信息_问题主题",
    "问题信息_故障现象描述", "故障现象描述", "问题信息_问题发生阶段",
    "问题发生阶段", "问题信息_客户行业", "客户行业", "问题信息_问题发生时间",
)
_MISSED_TEST_FIELDS = (
    "漏测流出原因", "问题流出原因", "漏测原因", "测试遗漏原因", "测试阶段",
    "流出环节", "分析结论", "原因分析",
)
_ALLOWED_FIELDS = {
    "SOFTWARE_ASSESSMENT": _ASSESSMENT_FIELDS,
    "RESOLUTION": _RESOLUTION_FIELDS,
    "ITR": _ITR_FIELDS,
    "MISSED_TEST": _MISSED_TEST_FIELDS,
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _source_revision(row: dict[str, Any]) -> str:
    return str(row.get("source_hash") or "") or hashlib.sha256(
        str(row.get("raw_json") or "{}").encode("utf-8")
    ).hexdigest()


def _source_ref(source_type: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_type": source_type,
        "source_id": str(row.get("material_id") or ""),
        "source_revision": _source_revision(row),
        "version_no": int(row.get("version_no") or 0),
        "business_key": str(row.get("business_key") or ""),
        "group_code": str(row.get("group_code") or ""),
    }


def _latest_per_group(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: (int(item.get("version_no") or 0), str(item.get("created_at") or ""), str(item.get("material_id") or "")), reverse=True):
        latest.setdefault(str(row.get("group_id") or ""), row)
    return list(latest.values())


def build_scenario_source_bundle_v1(
    material_repository: Any,
    software_assessment_record_id: str,
    *,
    trigger_source: str = "SOFTWARE_ASSESSMENT",
    trigger_reason: str = "QUALITY_SCENARIO_GENERATION",
) -> dict[str, Any]:
    """Build one deterministic bundle from an existing selected assessment row."""
    selected_id = str(software_assessment_record_id or "").strip()
    if not selected_id:
        raise ValueError("SOFTWARE_ASSESSMENT_RECORD_ID_REQUIRED")

    with material_repository.connect() as connection:
        assessment_row = connection.execute(
            """SELECT m.*, g.group_code FROM source_material m
               JOIN data_group g ON g.group_id=m.group_id
               WHERE m.material_id=?""",
            (selected_id,),
        ).fetchone()
        if not assessment_row:
            raise ValueError("SOFTWARE_ASSESSMENT_SOURCE_NOT_FOUND")
        assessment = dict(assessment_row)
        if assessment.get("material_type") != "SOFTWARE_OPERATION" or assessment.get("group_code") != "SW-OPS":
            raise ValueError("SOURCE_IS_NOT_SOFTWARE_ASSESSMENT")

        canonical = normalize_itr(assessment.get("canonical_itr") or assessment.get("business_key"))
        selected_sources: dict[str, dict[str, Any]] = {
            "SOFTWARE_ASSESSMENT": assessment,
        }
        source_candidates: dict[str, list[dict[str, Any]]] = {}
        for source_type, material_type in SOURCE_MATERIAL_TYPES.items():
            if source_type == "SOFTWARE_ASSESSMENT" or not canonical:
                source_candidates[source_type] = []
                continue
            rows = [dict(row) for row in connection.execute(
                """SELECT m.*, g.group_code FROM source_material m
                   JOIN data_group g ON g.group_id=m.group_id
                   WHERE m.material_type=? AND
                         UPPER(REPLACE(COALESCE(NULLIF(m.canonical_itr,''),m.business_key),' ',''))=?
                   ORDER BY m.version_no DESC,m.created_at DESC,m.material_id DESC""",
                (material_type, canonical),
            )]
            source_candidates[source_type] = _latest_per_group(rows)

    source_refs: dict[str, dict[str, Any]] = {}
    missing_information: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    for source_type in SOURCE_TYPES:
        candidates = [assessment] if source_type == "SOFTWARE_ASSESSMENT" else source_candidates[source_type]
        if not candidates:
            source_refs[source_type] = {"status": "MISSING", "records": []}
            if source_type == "SOFTWARE_ASSESSMENT":
                raise ValueError("SOFTWARE_ASSESSMENT_SOURCE_REQUIRED")
            missing_information.append({
                "source_type": source_type,
                "code": f"{source_type}_SOURCE_MISSING",
                "message": f"未找到{source_type}来源；不据此推断相关结论。",
            })
            continue
        if len(candidates) > 1:
            refs = [_source_ref(source_type, row) for row in candidates]
            source_refs[source_type] = {"status": "CONFLICT", "records": refs}
            warnings.append({
                "source_type": source_type,
                "code": f"{source_type}_SOURCE_AMBIGUOUS",
                "message": "存在多个来源分组记录，保持冲突状态，不自动选择。",
            })
            continue
        selected_sources[source_type] = candidates[0]
        source_refs[source_type] = {"status": "PRESENT", "records": [_source_ref(source_type, candidates[0])]}

    field_evidence: dict[str, dict[str, Any]] = {}
    field_values: dict[str, dict[str, Any]] = {}
    for source_type, row in selected_sources.items():
        raw = json.loads(row.get("raw_json") or "{}")
        if not isinstance(raw, dict):
            raw = {}
        allowed = set(_ALLOWED_FIELDS[source_type])
        captured = {key: value for key, value in raw.items() if key in allowed and value not in (None, "", [], {})}
        field_values[source_type] = captured
        ref = _source_ref(source_type, row)
        for field_name, value in captured.items():
            field_evidence[f"{source_type}.{field_name}"] = {
                **ref,
                "source_field": field_name,
                "value": value,
                "authority": source_type,
            }

    bundle_id = "SSB-" + hashlib.sha256(selected_id.encode("utf-8")).hexdigest()[:24]
    revision_material = {
        "contract_version": CONTRACT_VERSION,
        "bundle_id": bundle_id,
        "sources": {
            source_type: [
                {key: record.get(key) for key in ("source_id", "source_revision", "version_no")}
                for record in source_refs[source_type]["records"]
            ]
            for source_type in SOURCE_TYPES
        },
        "trigger_source": str(trigger_source or ""),
        "trigger_reason": str(trigger_reason or ""),
    }
    bundle_revision = hashlib.sha256(_json(revision_material).encode("utf-8")).hexdigest()
    return {
        "contract_version": CONTRACT_VERSION,
        "bundle_id": bundle_id,
        "bundle_revision": bundle_revision,
        "primary_source_type": "SOFTWARE_ASSESSMENT",
        "primary_source_id": selected_id,
        "trigger": {"source": str(trigger_source or ""), "reason": str(trigger_reason or "")},
        "authority": dict(FIELD_AUTHORITIES),
        "sources": source_refs,
        "field_values": field_values,
        "field_evidence": field_evidence,
        "missing_information": missing_information,
        "warnings": warnings,
    }


class ScenarioSourceBundleV1SnapshotStore:
    """Append-only snapshots in the existing mature source database."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS scenario_source_bundle_v1_snapshot (
                bundle_id TEXT NOT NULL,
                bundle_revision TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(bundle_id,bundle_revision)
            )""")
            connection.executescript("""
                CREATE TRIGGER IF NOT EXISTS scenario_source_bundle_v1_no_update
                BEFORE UPDATE ON scenario_source_bundle_v1_snapshot
                BEGIN SELECT RAISE(ABORT, 'SCENARIO_SOURCE_BUNDLE_SNAPSHOT_IMMUTABLE'); END;
                CREATE TRIGGER IF NOT EXISTS scenario_source_bundle_v1_no_delete
                BEFORE DELETE ON scenario_source_bundle_v1_snapshot
                BEGIN SELECT RAISE(ABORT, 'SCENARIO_SOURCE_BUNDLE_SNAPSHOT_IMMUTABLE'); END;
            """)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def save(self, bundle: dict[str, Any]) -> dict[str, Any]:
        bundle_id = str(bundle.get("bundle_id") or "")
        revision = str(bundle.get("bundle_revision") or "")
        if not bundle_id or not revision:
            raise ValueError("SCENARIO_SOURCE_BUNDLE_IDENTITY_REQUIRED")
        payload = _json(bundle)
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT snapshot_json FROM scenario_source_bundle_v1_snapshot WHERE bundle_id=? AND bundle_revision=?",
                (bundle_id, revision),
            ).fetchone()
            if existing:
                if existing["snapshot_json"] != payload:
                    raise ValueError("SCENARIO_SOURCE_BUNDLE_REVISION_COLLISION")
                return {"created": False, "bundle_id": bundle_id, "bundle_revision": revision}
            connection.execute(
                "INSERT INTO scenario_source_bundle_v1_snapshot(bundle_id,bundle_revision,snapshot_json) VALUES(?,?,?)",
                (bundle_id, revision, payload),
            )
        return {"created": True, "bundle_id": bundle_id, "bundle_revision": revision}

    def get(self, bundle_id: str, bundle_revision: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM scenario_source_bundle_v1_snapshot WHERE bundle_id=? AND bundle_revision=?",
                (bundle_id, bundle_revision),
            ).fetchone()
        return json.loads(row["snapshot_json"]) if row else None


__all__ = [
    "CONTRACT_VERSION",
    "FIELD_AUTHORITIES",
    "SOURCE_TYPES",
    "ScenarioSourceBundleV1SnapshotStore",
    "build_scenario_source_bundle_v1",
]
