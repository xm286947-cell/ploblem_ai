from __future__ import annotations

import sqlite3

import pytest

from quality_knowledge.materials import MaterialRepository
from quality_knowledge.scenario_source_bundle_v1 import (
    CONTRACT_VERSION,
    ScenarioSourceBundleV1SnapshotStore,
    build_scenario_source_bundle_v1,
)


def add(repo: MaterialRepository, group_code: str, business_key: str, raw: dict):
    return repo.add_material(repo.group(group_code), business_key, raw, "golden.xlsx", "data", 2)[0]


def test_bundle_captures_authority_provenance_missing_sources_and_immutable_snapshot(tmp_path):
    db = tmp_path / "mature.db"
    repo = MaterialRepository(db)
    assessment_id = add(repo, "SW-OPS", "ITR2026100001", {
        "问题信息_问题描述": "登录后软件无响应",
        "考核信息_考核状态": "已考核",
        "考核信息_考核结果": "需整改",
    })
    resolution_id = add(repo, "ITR-CS", "ITR2026100001CS", {
        "问题信息_问题原因定位": "缓存状态未清理",
        "问题处理结果_问题解决方案": "修复状态清理逻辑",
    })
    itr_id = add(repo, "ITR", "ITR2026100001", {
        "问题信息_问题发生阶段": "终端正常使用",
        "问题信息_故障现象描述": "界面无响应",
    })

    bundle = build_scenario_source_bundle_v1(repo, assessment_id)

    assert bundle["contract_version"] == CONTRACT_VERSION
    assert bundle["primary_source_type"] == "SOFTWARE_ASSESSMENT"
    assert bundle["primary_source_id"] == assessment_id
    assert bundle["sources"]["SOFTWARE_ASSESSMENT"]["status"] == "PRESENT"
    assert bundle["sources"]["RESOLUTION"]["records"][0]["source_id"] == resolution_id
    assert bundle["sources"]["ITR"]["records"][0]["source_id"] == itr_id
    assert bundle["sources"]["MISSED_TEST"]["status"] == "MISSING"
    assert any(item["code"] == "MISSED_TEST_SOURCE_MISSING" for item in bundle["missing_information"])
    assert bundle["field_evidence"]["SOFTWARE_ASSESSMENT.考核信息_考核结果"]["authority"] == "SOFTWARE_ASSESSMENT"
    assert bundle["field_evidence"]["RESOLUTION.问题信息_问题原因定位"]["source_id"] == resolution_id

    store = ScenarioSourceBundleV1SnapshotStore(db)
    assert store.save(bundle)["created"] is True
    assert store.save(bundle)["created"] is False
    assert store.get(bundle["bundle_id"], bundle["bundle_revision"]) == bundle


def test_bundle_revision_changes_with_source_revision_or_trigger(tmp_path):
    repo = MaterialRepository(tmp_path / "mature.db")
    assessment_id = add(repo, "SW-OPS", "ITR2026100002", {"问题信息_问题描述": "启动失败"})
    add(repo, "ITR-CS", "ITR2026100002CS", {"问题处理结果_问题解决方案": "首次修复"})
    first = build_scenario_source_bundle_v1(repo, assessment_id)
    add(repo, "ITR-CS", "ITR2026100002CS", {"问题处理结果_问题解决方案": "修复后复测通过"})
    revised = build_scenario_source_bundle_v1(repo, assessment_id)
    retriggered = build_scenario_source_bundle_v1(
        repo, assessment_id, trigger_source="USER_RETRY", trigger_reason="MANUAL_REANALYSIS"
    )

    assert first["bundle_id"] == revised["bundle_id"]
    assert first["bundle_revision"] != revised["bundle_revision"]
    assert revised["bundle_revision"] != retriggered["bundle_revision"]


def test_bundle_rejects_non_assessment_and_missing_primary_source(tmp_path):
    repo = MaterialRepository(tmp_path / "mature.db")
    itr_id = add(repo, "ITR", "ITR2026100003", {"问题信息_问题描述": "现场故障"})
    with pytest.raises(ValueError, match="SOURCE_IS_NOT_SOFTWARE_ASSESSMENT"):
        build_scenario_source_bundle_v1(repo, itr_id)
    with pytest.raises(ValueError, match="SOFTWARE_ASSESSMENT_SOURCE_NOT_FOUND"):
        build_scenario_source_bundle_v1(repo, "MAT-NOT-FOUND")


def test_bundle_snapshot_revision_is_append_only(tmp_path):
    db = tmp_path / "mature.db"
    repo = MaterialRepository(db)
    assessment_id = add(repo, "SW-OPS", "ITR2026100004", {"问题信息_问题描述": "服务异常"})
    bundle = build_scenario_source_bundle_v1(repo, assessment_id)
    store = ScenarioSourceBundleV1SnapshotStore(db)
    store.save(bundle)

    altered = {**bundle, "warnings": [{"code": "MUTATED"}]}
    with pytest.raises(sqlite3.IntegrityError, match="SNAPSHOT_IMMUTABLE"):
        with sqlite3.connect(db) as connection:
            connection.execute(
                "UPDATE scenario_source_bundle_v1_snapshot SET snapshot_json=? WHERE bundle_id=? AND bundle_revision=?",
                ('{}', bundle["bundle_id"], bundle["bundle_revision"]),
            )
    with pytest.raises(ValueError, match="REVISION_COLLISION"):
        store.save(altered)
    assert store.get(bundle["bundle_id"], bundle["bundle_revision"]) == bundle
