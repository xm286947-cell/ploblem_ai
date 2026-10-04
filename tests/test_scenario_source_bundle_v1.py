from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from quality_knowledge.materials import MaterialRepository
from quality_knowledge.scenario_generation import ScenarioGenerationService
from quality_knowledge.scenario_sources import _analysis_snapshot
from quality_knowledge.scenarios import ScenarioRepository
from quality_knowledge.scenario_source_bundle_v1 import (
    CONTRACT_VERSION,
    ScenarioSourceBundleV1SnapshotStore,
    build_scenario_source_bundle_v1,
)
from quality_knowledge.reverse_quality_bundle_adapter import reverse_quality_facts_from_bundle
from quality_knowledge.reverse_quality_bundle_bridge import ReverseQualityBundleBridge


def _add(repo: MaterialRepository, group: str, business_key: str, raw: dict):
    material_id = repo.add_material(repo.group(group), business_key, raw, "source.xlsx", "facts", 2)[0]
    with repo.connect() as connection:
        row = dict(connection.execute(
            "SELECT m.*,g.group_code FROM source_material m JOIN data_group g USING(group_id) WHERE material_id=?",
            (material_id,),
        ).fetchone())
    return row


def _ref(source_type: str, row: dict, relation_type="SUPPORTING_EVIDENCE"):
    return {
        "source_type": source_type,
        "source_id": row["material_id"],
        "source_revision": row["source_hash"],
        "version_no": row["version_no"],
        "business_key": row["business_key"],
        "group_code": row["group_code"],
        "relation_type": relation_type,
        "binding_status": "BOUND",
        "evidence_kind": "SOURCE_MATERIAL",
    }


def _snapshot(assessment: dict, refs: list[dict], **overrides):
    snapshot = {
        "selected_issue": {
            "knowledge_id": "QK-SW-1",
            "business_issue_id": "ITR2026100001",
            "software_assessment_record_id": assessment["material_id"],
            "software_assessment_revision": assessment["source_hash"],
            "product_code": "PLC",
            "product_model": "P100",
            "ipmt": "IPMT-A",
            "spdt": "SPDT-A",
            "industry": "工业",
            "customer": "客户甲",
            "kpi_year": "2026",
            "kpi_month": "10",
        },
        "source_refs": [_ref("SOFTWARE_ASSESSMENT", assessment, "PRIMARY_SOURCE"), *refs],
        "normalized_facts": {"issue_fact": {"title": "冻结时的问题标题"}},
        "effective_analysis": {
            "occurrence": {
                "analysis_run_id": "RUN-OCC-1", "analysis_revision": "occ-r1", "status": "COMPLETED",
                "issue_version_id": "QK-SW-1-V1", "input_hash": "occ-input", "result": {"root_cause_summary": "AI occurrence root"},
            },
            "escape": {
                "analysis_run_id": "RUN-ESC-1", "analysis_revision": "esc-r1", "status": "COMPLETED",
                "issue_version_id": "QK-SW-1-V1", "input_hash": "esc-input",
                "result": {"escape_cause_summary": "AI 结论", "verification_gap": "测试场景缺失", "expected_detection_stage": "系统测试"},
                "human_confirmations": [{
                    "question_key": "escape_cause_summary", "status": "CORRECTED",
                    "answer": "人工确认：需求评审遗漏", "evidence": "评审记录", "confirmed_by": "QA",
                }],
            },
            "recurrence": {
                "analysis_run_id": "RUN-REC-1", "analysis_revision": "rec-r1", "status": "COMPLETED",
                "issue_version_id": "QK-SW-1-V1", "input_hash": "rec-input", "result": {"recurrence_risk_level": "MEDIUM"},
            },
            "capability_gap": {
                "analysis_run_id": "RUN-GAP-1", "analysis_revision": "gap-r1", "status": "COMPLETED",
                "issue_version_id": "QK-SW-1-V1", "input_hash": "gap-input", "result": {"capability_gaps": [{"dimension": "测试"}]},
            },
        },
        "leakage_analysis": {
            "occurrence": {"root_cause_summary": "AI occurrence root"},
            "escape": {"escape_cause_summary": "AI 结论", "verification_gap": "测试场景缺失", "expected_detection_stage": "系统测试"},
            "recurrence": {"recurrence_risk_level": "MEDIUM"},
            "capability_gap": {"capability_gaps": [{"dimension": "测试"}]},
        },
        "snapshot_metadata": {
            "snapshot_id": "QSG-SNAPSHOT-1", "built_at": "2026-10-05T00:00:00Z",
            "builder_version": "scenario-source-snapshot/v1", "selected_issue_count": 1,
            "source_count": len(refs) + 1,
            "source_hashes": [assessment["source_hash"], *[ref["source_revision"] for ref in refs]],
        },
    }
    snapshot.update(overrides)
    return snapshot


def test_bundle_uses_only_frozen_source_refs_and_applies_frozen_authority_matrix(tmp_path):
    db = tmp_path / "mature.db"
    repo = MaterialRepository(db)
    assessment = _add(repo, "SW-OPS", "ITR2026100001", {
        "问题信息_问题描述": "考核源描述", "考核信息_考核状态": "已考核",
        "问题信息_产品型号": "P100", "问题信息_IPMT": "IPMT-A",
    })
    selected_resolution = _add(repo, "ITR-CS", "ITR2026100001CS", {
        "问题信息_问题描述": "Resolution authoritative description",
        "问题信息_客户名称": "Resolution customer", "问题信息_SPDT": "SPDT-R",
        "问题信息_问题原因定位": "Resolution root", "问题处理结果_问题解决方案": "Resolution fix",
        "验证结果": "Resolution verified",
    })
    unrelated_resolution = _add(repo, "ITR-CS", "ITR2026100001CS", {
        "问题信息_问题描述": "Must not be rediscovered", "问题信息_问题原因定位": "wrong root",
    })
    itr = _add(repo, "ITR", "ITR2026100001", {
        "问题信息_问题发生阶段": "终端正常使用", "问题信息_问题描述": "ITR description",
    })
    analysis_ref = {
        "source_type": "MISSED_TEST", "source_id": "RUN-ESC-1", "source_revision": "esc-r1",
        "version_no": 0, "business_key": "ITR2026100001", "group_code": "QUALITY_ISSUE_ANALYSIS",
        "relation_type": "EFFECTIVE_ANALYSIS", "binding_status": "BOUND", "evidence_kind": "EFFECTIVE_ANALYSIS",
    }
    snapshot = _snapshot(assessment, [_ref("RESOLUTION", selected_resolution), _ref("ITR", itr), analysis_ref])

    bundle = build_scenario_source_bundle_v1(snapshot, evidence_repository=repo)

    assert bundle["contract_version"] == CONTRACT_VERSION
    assert bundle["selected_issue"] == snapshot["selected_issue"]
    assert bundle["snapshot_metadata"] == snapshot["snapshot_metadata"]
    assert next(ref for ref in bundle["sources"] if ref["source_type"] == "RESOLUTION")["source_id"] == selected_resolution["material_id"]
    assert bundle["facts"]["problem_description"] == "Resolution authoritative description"
    assert bundle["facts"]["customer_context"]["customer"] == "Resolution customer"
    assert bundle["facts"]["organization_context"]["spdt"] == "SPDT-R"
    assert bundle["facts"]["root_cause"] == "Resolution root"
    assert bundle["facts"]["missed_test_cause"] == "人工确认：需求评审遗漏"
    assert bundle["facts"]["verification_gap"] == "测试场景缺失"
    assert set(bundle["effective_analysis"]) == {"occurrence", "escape", "recurrence", "capability_gap"}
    assert bundle["effective_analysis"]["escape"]["analysis_revision"] == "esc-r1"
    assert not any("Must not be rediscovered" in json.dumps(x, ensure_ascii=False) for x in bundle["field_values"]["RESOLUTION"])
    assert any(x["provenance"] == "HUMAN_CONFIRMED" for x in bundle["field_evidence"])
    assert bundle["authority"]["problem_product_customer_ipmt_spdt"] == ["RESOLUTION", "SOFTWARE_ASSESSMENT", "ITR"]


def test_bundle_marks_missing_sources_and_revision_includes_effective_analyses(tmp_path):
    repo = MaterialRepository(tmp_path / "mature.db")
    assessment = _add(repo, "SW-OPS", "ITR2026100002", {"问题信息_问题描述": "仅有考核记录"})
    snapshot = _snapshot(assessment, [], effective_analysis={}, leakage_analysis={})
    bundle = build_scenario_source_bundle_v1(snapshot, evidence_repository=repo)
    retriggered = build_scenario_source_bundle_v1(
        snapshot, evidence_repository=repo, trigger_source="USER_RETRY", trigger_reason="REANALYSIS"
    )
    changed_analysis = _snapshot(
        assessment, [],
        effective_analysis={"occurrence": {"analysis_run_id": "RUN-OCC-2", "analysis_revision": "occ-r2", "status": "COMPLETED", "result": {"root_cause_summary": "new"}}},
    )
    revised = build_scenario_source_bundle_v1(changed_analysis, evidence_repository=repo)

    assert bundle["source_status"]["SOFTWARE_ASSESSMENT"] == "PRESENT"
    assert bundle["source_status"]["RESOLUTION"] == "MISSING"
    assert bundle["source_status"]["MISSED_TEST"] == "MISSING"
    assert any(item["code"] == "MISSED_TEST_EFFECTIVE_ANALYSIS_MISSING" for item in bundle["missing_information"])
    assert bundle["bundle_revision"] != retriggered["bundle_revision"]
    assert bundle["bundle_revision"] != revised["bundle_revision"]
    assert "missed_test_cause" not in bundle["facts"]


def test_conflicted_resolutions_never_supply_facts_and_safe_fields_fallback(tmp_path):
    repo = MaterialRepository(tmp_path / "mature.db")
    assessment = _add(repo, "SW-OPS", "ITR2026100006", {
        "问题信息_问题描述": "考核源描述 fallback",
        "问题信息_产品型号": "P-SW",
        "问题信息_产品编码": "PLC-SW",
        "问题信息_客户名称": "考核客户",
        "问题信息_客户行业": "工业",
        "问题信息_IPMT": "IPMT-SW",
        "问题信息_SPDT": "SPDT-SW",
    })
    resolution_a = _add(repo, "ITR-CS", "ITR2026100006CS-A", {
        "问题信息_问题描述": "冲突 Resolution A 描述",
        "问题信息_产品型号": "P-A",
        "问题信息_客户名称": "客户 A",
        "问题信息_问题原因定位": "冲突根因 A",
        "问题处理结果_问题解决方案": "冲突措施 A",
    })
    resolution_b = _add(repo, "ITR-CS", "ITR2026100006CS-B", {
        "问题信息_问题描述": "冲突 Resolution B 描述",
        "问题信息_产品型号": "P-B",
        "问题信息_客户名称": "客户 B",
        "问题信息_问题原因定位": "冲突根因 B",
        "问题处理结果_问题解决方案": "冲突措施 B",
    })
    ref_a = _ref("RESOLUTION", resolution_a)
    ref_b = _ref("RESOLUTION", resolution_b)
    ref_a["binding_status"] = "CONFLICT"
    ref_b["binding_status"] = "CONFLICT"

    bundle = build_scenario_source_bundle_v1(
        _snapshot(assessment, [ref_a, ref_b]), evidence_repository=repo
    )

    assert bundle["source_status"]["RESOLUTION"] == "CONFLICT"
    assert bundle["facts"]["problem_description"] == "考核源描述 fallback"
    assert bundle["facts"]["product_context"] == {"product_model": "P-SW", "product_code": "PLC-SW"}
    assert bundle["facts"]["customer_context"] == {"customer": "考核客户", "industry": "工业"}
    assert bundle["facts"]["organization_context"] == {"ipmt": "IPMT-SW", "spdt": "SPDT-SW"}
    assert "root_cause" not in bundle["facts"]
    assert "corrective_actions" not in bundle["facts"]
    conflict_blockers = [
        item for item in bundle["missing_information"]
        if item["code"] == "SOURCE_RELATION_CONFLICT" and item["source_type"] == "RESOLUTION"
    ]
    assert len(conflict_blockers) == 1
    assert conflict_blockers[0]["severity"] == "BLOCKER"
    assert {"root_cause", "corrective_actions"}.issubset(conflict_blockers[0]["affected_fields"])
    assert "冲突根因 A" not in json.dumps(bundle["facts"], ensure_ascii=False)
    assert "冲突根因 B" not in json.dumps(bundle["facts"], ensure_ascii=False)


def test_bundle_adapter_preserves_frozen_provenance_and_never_reopens_sources(tmp_path):
    repo = MaterialRepository(tmp_path / "mature.db")
    assessment = _add(repo, "SW-OPS", "ITR2026100007", {
        "问题信息_问题描述": "冻结描述", "问题信息_产品型号": "P-SW",
    })
    resolution = _add(repo, "ITR-CS", "ITR2026100007CS", {
        "问题信息_问题原因定位": "冻结根因", "问题处理结果_问题解决方案": "冻结措施",
    })
    analysis_ref = {
        "source_type": "MISSED_TEST", "source_id": "RUN-ESC-1", "source_revision": "esc-r1",
        "version_no": 0, "business_key": "ITR2026100007", "group_code": "QUALITY_ISSUE_ANALYSIS",
        "relation_type": "EFFECTIVE_ANALYSIS", "binding_status": "BOUND",
        "evidence_kind": "EFFECTIVE_ANALYSIS", "analysis_type": "escape",
    }
    bundle = build_scenario_source_bundle_v1(
        _snapshot(assessment, [_ref("RESOLUTION", resolution), analysis_ref]), evidence_repository=repo
    )

    facts = reverse_quality_facts_from_bundle(bundle)
    # A source mutation after the immutable Bundle was built is intentionally
    # irrelevant: this adapter performs no repository lookup.
    with repo.connect() as connection:
        connection.execute("UPDATE source_material SET raw_json=? WHERE material_id=?", (
            json.dumps({"问题信息_问题原因定位": "后改根因"}, ensure_ascii=False), resolution["material_id"],
        ))
    facts_after_mutation = reverse_quality_facts_from_bundle(bundle)

    assert facts_after_mutation == facts
    assert facts["bundle_provenance"]["bundle_id"] == bundle["bundle_id"]
    assert facts["bundle_provenance"]["bundle_revision"] == bundle["bundle_revision"]
    assert facts["bundle_provenance"]["snapshot_id"] == bundle["snapshot_metadata"]["snapshot_id"]
    root_evidence = facts["evidence"][facts["bundle_facts"]["root_cause"]["evidence_ids"][0]]
    assert root_evidence["value"] == "冻结根因"
    assert root_evidence["source_type"] == "RESOLUTION"
    assert facts["bundle_facts"]["missed_test_cause"]["value"] == "人工确认：需求评审遗漏"


def test_bundle_adapter_fails_closed_on_duplicate_effective_analysis_stage(tmp_path):
    repo = MaterialRepository(tmp_path / "mature.db")
    assessment = _add(repo, "SW-OPS", "ITR2026100008", {"问题信息_问题描述": "冻结描述"})
    refs = []
    for run_id in ("RUN-ESC-A", "RUN-ESC-B"):
        refs.append({
            "source_type": "MISSED_TEST", "source_id": run_id, "source_revision": run_id + "-rev",
            "version_no": 0, "business_key": "ITR2026100008", "group_code": "QUALITY_ISSUE_ANALYSIS",
            "relation_type": "EFFECTIVE_ANALYSIS", "binding_status": "BOUND",
            "evidence_kind": "EFFECTIVE_ANALYSIS", "analysis_type": "escape",
        })
    bundle = build_scenario_source_bundle_v1(_snapshot(assessment, refs), evidence_repository=repo)

    with pytest.raises(ValueError, match="MULTIPLE_EFFECTIVE_ANALYSIS_REFS:escape"):
        reverse_quality_facts_from_bundle(bundle)


def test_bundle_bridge_does_not_invoke_runtime_for_conflict_blocker(tmp_path):
    repo = MaterialRepository(tmp_path / "mature.db")
    assessment = _add(repo, "SW-OPS", "ITR2026100009", {"问题信息_问题描述": "考核源描述"})
    resolution_a = _add(repo, "ITR-CS", "ITR2026100009CS-A", {"问题信息_问题原因定位": "根因 A"})
    resolution_b = _add(repo, "ITR-CS", "ITR2026100009CS-B", {"问题信息_问题原因定位": "根因 B"})
    ref_a = _ref("RESOLUTION", resolution_a); ref_a["binding_status"] = "CONFLICT"
    ref_b = _ref("RESOLUTION", resolution_b); ref_b["binding_status"] = "CONFLICT"
    analysis_ref = {
        "source_type": "MISSED_TEST", "source_id": "RUN-ESC-1", "source_revision": "esc-r1",
        "version_no": 0, "business_key": "ITR2026100009", "group_code": "QUALITY_ISSUE_ANALYSIS",
        "relation_type": "EFFECTIVE_ANALYSIS", "binding_status": "BOUND",
        "evidence_kind": "EFFECTIVE_ANALYSIS", "analysis_type": "escape",
    }
    bundle = build_scenario_source_bundle_v1(_snapshot(assessment, [ref_a, ref_b, analysis_ref]), evidence_repository=repo)
    bundle_store = ScenarioSourceBundleV1SnapshotStore(repo.db_path)
    bundle_store.save(bundle)

    class RuntimeForbiddenService:
        def get(self, _canonical):
            return None

        def _analyse_run(self, *_args):
            raise AssertionError("Runtime must not run for a conflict blocker")

    with pytest.raises(ValueError, match="INFORMATION_REQUIRED:SOURCE_RELATION_CONFLICT:RESOLUTION"):
        ReverseQualityBundleBridge(RuntimeForbiddenService(), bundle_store).analyse(bundle, taxonomy={})


def test_bundle_bridge_passes_only_bundle_facts_to_mature_service(tmp_path):
    repo = MaterialRepository(tmp_path / "mature.db")
    assessment = _add(repo, "SW-OPS", "ITR2026100010", {
        "问题信息_问题描述": "Bundle 描述", "问题信息_产品型号": "P-SW",
    })
    resolution = _add(repo, "ITR-CS", "ITR2026100010CS", {"问题信息_问题原因定位": "Bundle 根因"})
    analysis_ref = {
        "source_type": "MISSED_TEST", "source_id": "RUN-ESC-1", "source_revision": "esc-r1",
        "version_no": 0, "business_key": "ITR2026100010", "group_code": "QUALITY_ISSUE_ANALYSIS",
        "relation_type": "EFFECTIVE_ANALYSIS", "binding_status": "BOUND",
        "evidence_kind": "EFFECTIVE_ANALYSIS", "analysis_type": "escape",
    }
    bundle = build_scenario_source_bundle_v1(
        _snapshot(assessment, [_ref("RESOLUTION", resolution), analysis_ref]), evidence_repository=repo
    )
    bundle_store = ScenarioSourceBundleV1SnapshotStore(repo.db_path)
    bundle_store.save(bundle)

    class Repository:
        def __init__(self):
            self.input = None

        def start_run(self, **kwargs):
            self.input = kwargs["input_payload"]
            return {"run_id": "RQ-RUN-BUNDLE"}

        def fail_run(self, *_args):
            raise AssertionError("unexpected failure")

    class Scenarios:
        @staticmethod
        def taxonomy_active(_product):
            return {"version_id": "TAX-1"}

    class MatureService:
        def __init__(self):
            self.repository = Repository()
            self.scenarios = Scenarios()

        @staticmethod
        def get(_canonical):
            return None

        @staticmethod
        def _analyse_run(facts, _taxonomy, _source_hash, _run_id, _product_code):
            return {"input": facts}

    service = MatureService()
    result = ReverseQualityBundleBridge(service, bundle_store).analyse(bundle)

    assert result["input"]["bundle_provenance"]["bundle_revision"] == bundle["bundle_revision"]
    assert result["input"]["bundle_facts"]["problem_description"]["value"] == "Bundle 描述"
    assert result["input"]["bundle_facts"]["root_cause"]["value"] == "Bundle 根因"
    assert service.repository.input["bundle_provenance"]["snapshot_id"] == bundle["snapshot_metadata"]["snapshot_id"]

    altered = json.loads(json.dumps(bundle, ensure_ascii=False))
    altered["facts"]["problem_description"] = "未冻结的新描述"
    with pytest.raises(ValueError, match="SCENARIO_SOURCE_BUNDLE_SNAPSHOT_MISMATCH"):
        ReverseQualityBundleBridge(service, bundle_store).analyse(altered)


def test_bundle_requires_a_frozen_snapshot_and_selected_assessment_locator(tmp_path):
    repo = MaterialRepository(tmp_path / "mature.db")
    with pytest.raises(ValueError, match="FROZEN_SOURCE_SNAPSHOT_SELECTED_ISSUE_REQUIRED"):
        build_scenario_source_bundle_v1({}, evidence_repository=repo)
    with pytest.raises(ValueError, match="FROZEN_SOURCE_SNAPSHOT_SOURCE_REFS_REQUIRED"):
        build_scenario_source_bundle_v1({"selected_issue": {"software_assessment_record_id": "MAT-X"}}, evidence_repository=repo)


def test_snapshot_source_revision_mismatch_fails_closed(tmp_path):
    repo = MaterialRepository(tmp_path / "mature.db")
    assessment = _add(repo, "SW-OPS", "ITR2026100003", {"问题信息_问题描述": "冻结前事实"})
    snapshot = _snapshot(assessment, [])
    snapshot["source_refs"][0]["source_revision"] = "stale-revision"
    with pytest.raises(ValueError, match="SOURCE_CHANGED_DURING_BUILD"):
        build_scenario_source_bundle_v1(snapshot, evidence_repository=repo)


def test_generation_service_builds_bundle_from_persisted_frozen_snapshot(tmp_path):
    db = tmp_path / "mature.db"
    repo = MaterialRepository(db)
    assessment = _add(repo, "SW-OPS", "ITR2026100005", {"问题信息_问题描述": "snapshot input"})
    record = _snapshot(assessment, [])
    record.pop("snapshot_metadata")
    scenario_repo = ScenarioRepository(db)
    service = ScenarioGenerationService(None, scenario_repo, tmp_path)

    service.save_source_snapshot("QSG-FROZEN-1", [record])
    frozen = service.source_snapshot("QSG-FROZEN-1")
    bundles = service.snapshot_source_bundles(frozen)

    assert frozen[0]["snapshot_metadata"]["snapshot_id"] == "QSG-FROZEN-1"
    assert bundles[0]["selected_issue"] == frozen[0]["selected_issue"]
    assert service.source_bundle_snapshots.get(bundles[0]["bundle_id"], bundles[0]["bundle_revision"]) == bundles[0]


def test_legacy_effective_analysis_snapshot_keeps_four_stages_and_human_override():
    class IssueService:
        repository = SimpleNamespace(get_human_confirmations=lambda _kid: [{
            "stage": "escape", "question_key": "escape_cause_summary", "status": "CORRECTED",
            "answer": "人工确认结论", "evidence": "评审记录", "confirmed_by": "QA",
        }])

        @staticmethod
        def get_latest_analysis(_kid, stage):
            if stage == "escape":
                return {
                    "analysis_run_id": "RUN-ESC", "analysis_type": "escape", "issue_version_id": "ISSUE-V1",
                    "status": "COMPLETED", "input_hash": "input-hash", "result": {"escape_cause_summary": "AI 结论"},
                }
            return None

    results, provenance = _analysis_snapshot(SimpleNamespace(issues=IssueService()), {"knowledge_id": "ISSUE-1"})

    assert set(provenance) == {"occurrence", "escape", "recurrence", "capability_gap"}
    assert provenance["occurrence"]["status"] == "MISSING"
    assert provenance["escape"]["status"] == "COMPLETED"
    assert provenance["escape"]["result"]["escape_cause_summary"] == "AI 结论"
    assert provenance["escape"]["effective_result"]["escape_cause_summary"] == "人工确认结论"
    assert provenance["escape"]["human_confirmations"][0]["evidence"] == "评审记录"
    assert results["escape"]["escape_cause_summary"] == "AI 结论"


def test_bundle_snapshot_revision_is_append_only(tmp_path):
    db = tmp_path / "mature.db"
    repo = MaterialRepository(db)
    assessment = _add(repo, "SW-OPS", "ITR2026100004", {"问题信息_问题描述": "服务异常"})
    bundle = build_scenario_source_bundle_v1(_snapshot(assessment, []), evidence_repository=repo)
    store = ScenarioSourceBundleV1SnapshotStore(db)
    assert store.save(bundle)["created"] is True
    assert store.save(bundle)["created"] is False
    altered = {**bundle, "warnings": [{"code": "MUTATED"}]}
    with pytest.raises(sqlite3.IntegrityError, match="SNAPSHOT_IMMUTABLE"):
        with sqlite3.connect(db) as connection:
            connection.execute(
                "UPDATE scenario_source_bundle_v1_snapshot SET snapshot_json=? WHERE bundle_id=? AND bundle_revision=?",
                ("{}", bundle["bundle_id"], bundle["bundle_revision"]),
            )
    with pytest.raises(ValueError, match="REVISION_COLLISION"):
        store.save(altered)
    assert store.get(bundle["bundle_id"], bundle["bundle_revision"]) == bundle
