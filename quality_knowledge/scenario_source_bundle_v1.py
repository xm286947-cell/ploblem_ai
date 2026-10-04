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

CONTRACT_VERSION = "scenario-source-bundle/v1"
SOURCE_TYPES = ("SOFTWARE_ASSESSMENT", "RESOLUTION", "ITR", "MISSED_TEST")

# Frozen design authority matrix. The production root determines selection,
# while factual fields follow their own source precedence.
FIELD_AUTHORITIES = {
    "selection_identity_and_scope": ["SOFTWARE_ASSESSMENT"],
    "problem_product_customer_ipmt_spdt": ["RESOLUTION", "SOFTWARE_ASSESSMENT", "ITR"],
    "root_cause_and_corrective_actions": ["RESOLUTION"],
    "missed_test_and_verification_gap": ["MISSED_TEST_EFFECTIVE_ANALYSIS"],
    "human_confirmation": "OVERRIDES_AI_INFERENCE_WHEN_FIELD_KEY_MATCHES",
    "conflict_policy": "PRESERVE_REFERENCES_AND_WARN",
}

_ASSESSMENT_FIELDS = (
    "业务键", "问题描述", "问题信息_问题描述", "问题现象", "问题信息_问题现象",
    "考核信息_考核状态", "流程信息_考核状态", "考核状态", "考核信息_考核结果",
    "考核信息_考核结论", "考核结果", "考核结论", "考核意见", "责任信息_责任人",
    "责任人", "责任信息_责任部门", "责任部门", "考核信息_考核分", "考核分",
    "产品型号", "问题信息_产品型号", "客户名称", "问题信息_客户名称",
    "客户行业", "问题信息_客户行业", "IPMT", "问题信息_IPMT", "SPDT", "问题信息_SPDT",
    "问题信息_产品编码", "产品编码", "问题信息_产品类型", "产品类型",
)
_RESOLUTION_FIELDS = (
    "问题信息_问题原因定位", "问题原因定位", "技术根因分析与纠正_TRC根因",
    "技术根因分析与纠正_TRC纠正信息", "问题处理结果_问题解决方案", "问题解决方案",
    "恢复措施", "恢复措施执行_现场作业记录", "验证结果", "问题验证结论", "复测结果", "验证情况",
    "问题信息_产品型号", "产品型号", "问题信息_产品编码", "产品编码", "问题信息_产品类型", "产品类型",
    "问题信息_客户名称", "客户名称", "问题信息_客户行业", "客户行业",
    "问题信息_IPMT", "IPMT", "问题信息_SPDT", "SPDT",
)
_ITR_FIELDS = (
    "问题信息_ITR单号", "ITR单号", "问题信息_问题描述", "问题信息_问题主题",
    "问题信息_故障现象描述", "故障现象描述", "问题信息_问题发生阶段",
    "问题发生阶段", "问题信息_客户行业", "客户行业", "问题信息_问题发生时间",
    "问题发生时间", "问题信息_产品型号", "产品型号", "问题信息_产品编码", "产品编码",
    "问题信息_产品类型", "产品类型", "问题信息_客户名称", "客户名称",
    "问题信息_IPMT", "IPMT", "问题信息_SPDT", "SPDT",
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


def _raw_value(raw: dict[str, Any], aliases: tuple[str, ...]) -> tuple[Any, str]:
    for alias in aliases:
        value = raw.get(alias)
        if value not in (None, "", [], {}):
            return value, alias
    return None, ""


def build_scenario_source_bundle_v1(
    frozen_source_snapshot: dict[str, Any],
    *,
    evidence_repository: Any = None,
    trigger_source: str = "SOFTWARE_ASSESSMENT",
    trigger_reason: str = "QUALITY_SCENARIO_GENERATION",
) -> dict[str, Any]:
    """Build from a frozen selection record; the DB may only resolve frozen IDs."""
    snapshot = dict(frozen_source_snapshot or {})
    selected_issue = snapshot.get("selected_issue")
    if not isinstance(selected_issue, dict):
        raise ValueError("FROZEN_SOURCE_SNAPSHOT_SELECTED_ISSUE_REQUIRED")
    selected_id = str(selected_issue.get("software_assessment_record_id") or "").strip()
    if not selected_id:
        raise ValueError("SOFTWARE_ASSESSMENT_RECORD_ID_REQUIRED")

    frozen_refs = snapshot.get("source_refs")
    if not isinstance(frozen_refs, list):
        raise ValueError("FROZEN_SOURCE_SNAPSHOT_SOURCE_REFS_REQUIRED")
    refs_by_type = {source_type: [] for source_type in SOURCE_TYPES}
    for ref in frozen_refs:
        if isinstance(ref, dict) and ref.get("source_type") in refs_by_type:
            refs_by_type[ref["source_type"]].append(dict(ref))
    assessment_refs = [ref for ref in refs_by_type["SOFTWARE_ASSESSMENT"] if ref.get("source_id") == selected_id]
    if len(assessment_refs) != 1 or assessment_refs[0].get("binding_status") == "CONFLICT":
        raise ValueError("SOFTWARE_ASSESSMENT_SOURCE_REQUIRED")

    # Exact-ID/revision reads enrich frozen references with raw evidence only.
    # No canonical-key search, join, or relationship selection is allowed here.
    material_refs = [ref for group in refs_by_type.values() for ref in group if ref.get("evidence_kind", "SOURCE_MATERIAL") == "SOURCE_MATERIAL"]
    located: dict[str, dict[str, Any]] = {}
    if material_refs:
        if evidence_repository is None:
            raise ValueError("SOURCE_EVIDENCE_REPOSITORY_REQUIRED")
        ids = list(dict.fromkeys(str(ref.get("source_id") or "") for ref in material_refs if ref.get("source_id")))
        marks = ",".join("?" for _ in ids)
        with evidence_repository.connect() as connection:
            rows = [dict(row) for row in connection.execute(
                f"SELECT m.*,g.group_code FROM source_material m JOIN data_group g ON g.group_id=m.group_id WHERE m.material_id IN ({marks})",
                tuple(ids),
            )] if ids else []
        located = {str(row["material_id"]): row for row in rows}
    for ref in material_refs:
        row = located.get(str(ref.get("source_id") or ""))
        if not row:
            raise ValueError("FROZEN_SOURCE_LOCATOR_NOT_FOUND")
        if _source_revision(row) != str(ref.get("source_revision") or ""):
            raise ValueError("SOURCE_CHANGED_DURING_BUILD")

    sources: dict[str, dict[str, Any]] = {}
    missing_information: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    for source_type in SOURCE_TYPES:
        records = refs_by_type[source_type]
        if source_type == "SOFTWARE_ASSESSMENT":
            records = assessment_refs
        conflict = any(ref.get("binding_status") == "CONFLICT" for ref in records)
        has_effective_missed_test = any(
            ref.get("source_type") == "MISSED_TEST" and ref.get("evidence_kind") == "EFFECTIVE_ANALYSIS"
            for ref in records
        )
        if source_type == "MISSED_TEST" and not has_effective_missed_test:
            missing_information.append({
                "source_type": source_type,
                "code": "MISSED_TEST_EFFECTIVE_ANALYSIS_MISSING",
                "message": "没有有效漏测分析结论；不得推断或补造漏测原因。",
            })
        if not records:
            sources[source_type] = {"status": "MISSING", "records": []}
            if source_type != "SOFTWARE_ASSESSMENT":
                missing_information.append({
                    "source_type": source_type,
                    "code": f"{source_type}_SOURCE_MISSING",
                    "message": f"未在冻结 Source Snapshot 中绑定 {source_type} 来源。",
                })
            continue
        if conflict:
            sources[source_type] = {"status": "CONFLICT", "records": records}
            warnings.append({
                "source_type": source_type,
                "code": "SOURCE_RELATION_CONFLICT",
                "message": "冻结快照记录了来源关系冲突，Bundle 不重新选择来源。",
            })
        else:
            sources[source_type] = {"status": "PRESENT", "records": records}

    raw_by_type: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {source_type: [] for source_type in SOURCE_TYPES}
    field_values: dict[str, list[dict[str, Any]]] = {source_type: [] for source_type in SOURCE_TYPES}
    for source_type in SOURCE_TYPES:
        for ref in refs_by_type[source_type]:
            if ref.get("evidence_kind", "SOURCE_MATERIAL") != "SOURCE_MATERIAL":
                continue
            row = located[str(ref["source_id"])]
            raw = json.loads(row.get("raw_json") or "{}")
            raw = raw if isinstance(raw, dict) else {}
            raw_by_type[source_type].append((ref, raw))
            allowed = set(_ALLOWED_FIELDS[source_type])
            field_values[source_type].append({key: value for key, value in raw.items() if key in allowed and value not in (None, "", [], {})})

    evidence: list[dict[str, Any]] = []
    facts: dict[str, Any] = {}

    def material_fact(target: str, aliases: tuple[str, ...], preference: tuple[str, ...]):
        for source_type in preference:
            for ref, raw in raw_by_type[source_type]:
                value, source_field = _raw_value(raw, aliases)
                if value in (None, "", [], {}):
                    continue
                facts[target] = value
                evidence.append({
                    "target_field": target, "source_type": source_type,
                    "source_id": ref.get("source_id"), "source_revision": ref.get("source_revision"),
                    "source_field": source_field,
                    "evidence_id": f"{source_type}:{ref.get('source_id')}:{ref.get('source_revision')}:{source_field}",
                    "excerpt_or_digest": value, "provenance": "SOURCE_FACT",
                })
                return

    material_fact("problem_description", ("问题信息_问题描述", "问题信息_问题主题", "问题描述", "问题现象"), ("RESOLUTION", "SOFTWARE_ASSESSMENT", "ITR"))
    context_fields = {
        "product_model": ("问题信息_产品型号", "产品型号"),
        "product_code": ("问题信息_产品编码", "产品编码", "问题信息_产品类型", "产品类型"),
        "customer": ("问题信息_客户名称", "客户名称"),
        "industry": ("问题信息_客户行业", "客户行业"),
        "ipmt": ("问题信息_IPMT", "IPMT"),
        "spdt": ("问题信息_SPDT", "SPDT"),
    }
    product_context = {}; customer_context = {}; organization_context = {}
    for target, aliases in context_fields.items():
        material_fact(target, aliases, ("RESOLUTION", "SOFTWARE_ASSESSMENT", "ITR"))
        if target in facts:
            if target in {"product_model", "product_code"}: product_context[target] = facts[target]
            elif target in {"customer", "industry"}: customer_context[target] = facts[target]
            else: organization_context[target] = facts[target]
    facts["product_context"] = product_context
    facts["customer_context"] = customer_context
    facts["organization_context"] = organization_context
    material_fact("occurrence_context", ("问题信息_问题发生阶段", "问题发生阶段", "问题信息_问题发生时间", "问题发生时间"), ("ITR", "RESOLUTION", "SOFTWARE_ASSESSMENT"))
    material_fact("root_cause", ("问题信息_问题原因定位", "问题原因定位", "技术根因分析与纠正_TRC根因", "TRC根因"), ("RESOLUTION",))
    material_fact("corrective_actions", ("问题处理结果_问题解决方案", "问题解决方案", "技术根因分析与纠正_TRC纠正信息", "TRC纠正信息", "恢复措施"), ("RESOLUTION",))
    material_fact("verification_result", ("验证结果", "问题验证结论", "复测结果", "验证情况"), ("RESOLUTION",))

    analysis = snapshot.get("effective_analysis") or snapshot.get("analysis_provenance") or {}
    leakage = snapshot.get("leakage_analysis") or {}
    escape_analysis = analysis.get("escape") or {}
    escape_result = escape_analysis.get("effective_result") or escape_analysis.get("result") or leakage.get("escape") or {}
    human_confirmations = escape_analysis.get("human_confirmations") or []
    missed_aliases = {
        "missed_test_cause": ("escape_cause_summary", "escape_reason", "missed_test_cause"),
        "verification_gap": ("verification_gap", "missing_control"),
        "expected_detection_stage": ("expected_detection_stage",),
        "actual_detection_stage": ("actual_detection_stage",),
    }
    for target, aliases in missed_aliases.items():
        picked = None; picked_key = ""; provenance = "EFFECTIVE_ANALYSIS"
        for confirmation in human_confirmations:
            key = str(confirmation.get("question_key") or "")
            if key in aliases and str(confirmation.get("status") or "").upper() in {"CONFIRMED", "CORRECTED"} and confirmation.get("answer"):
                picked = confirmation["answer"]; picked_key = key; provenance = "HUMAN_CONFIRMED"
                break
        if picked is None:
            picked, picked_key = _raw_value(escape_result if isinstance(escape_result, dict) else {}, aliases)
        if picked in (None, "", [], {}):
            continue
        facts[target] = picked
        analysis_ref = next((ref for ref in refs_by_type["MISSED_TEST"] if ref.get("evidence_kind") == "EFFECTIVE_ANALYSIS"), {})
        evidence.append({
            "target_field": target, "source_type": "MISSED_TEST",
            "source_id": analysis_ref.get("source_id") or "", "source_revision": analysis_ref.get("source_revision") or "",
            "source_field": picked_key,
            "evidence_id": f"MISSED_TEST:{analysis_ref.get('source_id','')}:{analysis_ref.get('source_revision','')}:{picked_key}",
            "excerpt_or_digest": picked, "provenance": provenance,
        })
    facts["missed_test_analysis"] = {
        stage: {
            "analysis_run_id": item.get("analysis_run_id") or "",
            "analysis_revision": item.get("analysis_revision") or "",
            "status": item.get("status") or "",
            "provenance": {key: item.get(key) for key in ("analysis_type", "issue_version_id", "input_hash", "started_at", "completed_at")},
            "result": item.get("result") or leakage.get(stage) or {},
            "effective_result": item.get("effective_result") or item.get("result") or leakage.get(stage) or {},
            "human_confirmations": item.get("human_confirmations") or [],
        }
        for stage, item in analysis.items()
    }
    facts["normalized_facts"] = snapshot.get("normalized_facts") or {}

    snapshot_metadata = snapshot.get("snapshot_metadata") or {}
    analysis_revisions = {stage: item.get("analysis_revision") or "" for stage, item in analysis.items()}
    bundle_id = "SSB-" + hashlib.sha256(selected_id.encode("utf-8")).hexdigest()[:24]
    flat_sources = []
    source_status = {}
    authoritative_fields = {
        "SOFTWARE_ASSESSMENT": ["selection_identity_and_scope"],
        "RESOLUTION": ["problem_product_customer_ipmt_spdt", "root_cause_and_corrective_actions"],
        "ITR": ["problem_product_customer_ipmt_spdt", "occurrence_context"],
        "MISSED_TEST": ["missed_test_and_verification_gap"],
    }
    captured_at = str(snapshot_metadata.get("built_at") or "")
    for source_type in SOURCE_TYPES:
        source_status[source_type] = sources[source_type]["status"]
        records = sources[source_type]["records"]
        if records:
            for ref in records:
                flat_sources.append({
                    **ref,
                    "status": sources[source_type]["status"],
                    "authoritative_fields": authoritative_fields[source_type],
                    "source_locator": (
                        f"source_material://{ref.get('source_id')}"
                        if ref.get("evidence_kind", "SOURCE_MATERIAL") == "SOURCE_MATERIAL"
                        else f"analysis_run://{ref.get('source_id')}"
                    ),
                    "captured_at": captured_at,
                })
        else:
            flat_sources.append({
                "source_type": source_type, "source_id": "", "source_revision": "",
                "version_no": 0, "relation_type": "SUPPORTING_EVIDENCE", "status": "MISSING",
                "authoritative_fields": authoritative_fields[source_type], "source_locator": "",
                "captured_at": captured_at,
            })
    revision_material = {
        "contract_version": CONTRACT_VERSION, "bundle_id": bundle_id,
        "sources": {
            source_type: [{key: record.get(key) for key in ("source_id", "source_revision", "version_no")} for record in sources[source_type]["records"]]
            for source_type in SOURCE_TYPES
        },
        "analysis_revisions": analysis_revisions,
        "trigger_source": str(trigger_source or ""), "trigger_reason": str(trigger_reason or ""),
    }
    bundle_revision = hashlib.sha256(_json(revision_material).encode("utf-8")).hexdigest()
    return {
        "contract_version": CONTRACT_VERSION, "bundle_id": bundle_id, "bundle_revision": bundle_revision,
        "selected_issue": selected_issue, "primary_source_type": "SOFTWARE_ASSESSMENT", "primary_source_id": selected_id,
        "trigger": {"trigger_source": str(trigger_source or ""), "trigger_reason": str(trigger_reason or "")},
        "authority": dict(FIELD_AUTHORITIES),
        "sources": flat_sources, "source_status": source_status,
        "facts": facts, "field_values": field_values, "field_evidence": evidence,
        "effective_analysis": facts["missed_test_analysis"],
        "analysis_revisions": analysis_revisions,
        "missing_information": missing_information, "warnings": warnings,
        "snapshot_metadata": snapshot_metadata,
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
