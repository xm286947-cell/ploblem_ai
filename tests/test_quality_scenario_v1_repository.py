import sqlite3

import pytest

from quality_knowledge.quality_scenario_v1 import (
    ScenarioCandidateV1,
    ScenarioConfirmationMetadata,
    ScenarioEvidenceReference,
    ScenarioReviewMetadata,
    ScenarioReviewStatus,
    ScenarioSourceReference,
    ScenarioStatus,
    ScenarioVersionMetadata,
)
from quality_knowledge.quality_scenario_v1_store import (
    SQLiteQualityScenarioV1Repository,
)


def candidate(candidate_id="C-1", product="PLC"):
    return ScenarioCandidateV1(
        candidate_id=candidate_id,
        product_code=product,
        lifecycle_stage_code="RUNTIME_EXECUTION",
        lifecycle_stage_name="运行执行",
        business_activity_code="POWER_LOSS_RETENTION_RECOVERY",
        business_activity_name="掉电数据保持与上电恢复",
        scenario_name="掉电恢复",
        scenario_description="运行中异常掉电后数据恢复",
        quality_concern_code="DATA_INTEGRITY",
        quality_concern_name="数据完整性",
        trigger_source="HIGH_PERCEPTION",
        trigger_reason="客户生产中断，属于高感知质量问题",
        trigger_condition="运行中异常掉电",
        expected_result="重新上电后数据正确恢复",
        applicability_scope="PLC",
        source_problem_refs=[
            ScenarioSourceReference(
                source_ref=f"ITR:{candidate_id}",
                source_type="ITR",
                source_id=candidate_id,
                canonical_itr=candidate_id,
                product_code=product,
            )
        ],
        evidence_refs=[
            ScenarioEvidenceReference(
                evidence_id=f"{candidate_id}.description",
                source_ref=f"ITR:{candidate_id}",
                evidence_type="FIELD",
                content_ref=f"evidence://{candidate_id}/description",
                supports=["scenario_description", "expected_result"],
                source_type="FACT",
            )
        ],
    )


def test_repository_candidate_crud_roundtrip(tmp_path):
    repo = SQLiteQualityScenarioV1Repository(tmp_path / "quality_scenario_v1.db")
    created = repo.create_from_candidate(candidate(), scenario_id="QSV1-1")
    assert created.status == ScenarioStatus.CANDIDATE
    assert repo.get("QSV1-1") == created
    assert repo.delete("QSV1-1") is True
    assert repo.get("QSV1-1") is None
    assert repo.delete("QSV1-1") is False


def test_repository_query_dimensions(tmp_path):
    repo = SQLiteQualityScenarioV1Repository(tmp_path / "quality_scenario_v1.db")
    repo.create_from_candidate(candidate("ITR-1", "PLC"), scenario_id="QSV1-PLC")
    hmi = candidate("ITR-2", "HMI").model_copy(
        update={
            "business_activity_code": "HMI_HOST_INTEGRATION",
            "business_activity_name": "PLC与HMI联动",
            "scenario_name": "HMI联动恢复",
            "quality_concern_code": "RECOVERY",
            "quality_concern_name": "可恢复性",
        }
    )
    repo.create_from_candidate(hmi, scenario_id="QSV1-HMI")

    assert [x.scenario_id for x in repo.list(product_code="HMI")] == ["QSV1-HMI"]
    assert [x.scenario_id for x in repo.list(
        product_code="PLC",
        lifecycle_stage_code="RUNTIME_EXECUTION",
        business_activity_code="POWER_LOSS_RETENTION_RECOVERY",
        quality_concern_code="DATA_INTEGRITY",
        status="CANDIDATE",
    )] == ["QSV1-PLC"]
    assert [x.scenario_id for x in repo.list(q="联动")] == ["QSV1-HMI"]


def test_repository_enforces_candidate_confirmed_published_path(tmp_path):
    repo = SQLiteQualityScenarioV1Repository(tmp_path / "quality_scenario_v1.db")
    item = repo.create_from_candidate(candidate(), scenario_id="QSV1-STATE")

    confirmed = item.model_copy(
        update={
            "status": ScenarioStatus.CONFIRMED,
            "scenario_version": 2,
            "review": ScenarioReviewMetadata(
                review_status=ScenarioReviewStatus.CONFIRMED,
                reviewer="QUALITY_OWNER",
                reviewed_at="2026-09-23T12:00:00Z",
            ),
            "confirmation": ScenarioConfirmationMetadata(
                quality_confirmed_by="QUALITY_OWNER",
                quality_confirmed_at="2026-09-23T12:00:00Z",
                technical_confirmed_by="RND_OWNER",
                technical_confirmed_at="2026-09-23T12:05:00Z",
                confirmation_note="专业质量确认场景事实；研发确认技术判断",
            ),
            "version": ScenarioVersionMetadata(
                created_by=item.version.created_by,
                created_at=item.version.created_at,
                updated_at="2026-09-23T12:00:00Z",
                parent_scenario_version=1,
                change_summary="人工确认",
            ),
        }
    )
    saved = repo.save(confirmed, actor="HUMAN")
    assert saved.status == ScenarioStatus.CONFIRMED
    assert saved.confirmation.quality_confirmed_by == "QUALITY_OWNER"
    assert saved.confirmation.technical_confirmed_by == "RND_OWNER"

    with repo.connect() as connection:
        review_rows = connection.execute(
            "SELECT review_json FROM quality_scenario_v1_review WHERE scenario_id=?",
            ("QSV1-STATE",),
        ).fetchall()
    assert review_rows
    assert '"quality_confirmed_by": "QUALITY_OWNER"' in review_rows[-1][0]
    assert '"technical_confirmed_by": "RND_OWNER"' in review_rows[-1][0]

    published = saved.model_copy(
        update={
            "status": ScenarioStatus.PUBLISHED,
            "scenario_version": 3,
            "version": ScenarioVersionMetadata(
                created_by=saved.version.created_by,
                created_at=saved.version.created_at,
                updated_at="2026-09-23T12:10:00Z",
                published_at="2026-09-23T12:10:00Z",
                parent_scenario_version=2,
                change_summary="正式发布",
            ),
        }
    )
    saved = repo.save(published, actor="SYSTEM")
    assert saved.status == ScenarioStatus.PUBLISHED

    with repo.connect() as connection:
        versions = connection.execute(
            "SELECT scenario_version FROM quality_scenario_v1_version WHERE scenario_id=? ORDER BY scenario_version",
            ("QSV1-STATE",),
        ).fetchall()
    assert [row[0] for row in versions] == [1, 2, 3]


def test_repository_rejects_direct_initial_publish(tmp_path):
    repo = SQLiteQualityScenarioV1Repository(tmp_path / "quality_scenario_v1.db")
    base = repo.create_from_candidate(candidate(), scenario_id="TEMP")
    published = base.model_copy(
        update={
            "scenario_id": "DIRECT",
            "status": ScenarioStatus.PUBLISHED,
            "review": ScenarioReviewMetadata(
                review_status="CONFIRMED",
                reviewer="OWNER",
                reviewed_at="2026-09-23T12:00:00Z",
            ),
            "confirmation": ScenarioConfirmationMetadata(
                quality_confirmed_by="OWNER",
                quality_confirmed_at="2026-09-23T12:00:00Z",
                technical_confirmed_by="RND_OWNER",
                technical_confirmed_at="2026-09-23T12:01:00Z",
            ),
            "version": ScenarioVersionMetadata(
                published_at="2026-09-23T12:00:00Z"
            ),
        }
    )
    with pytest.raises(ValueError, match="SCENARIO_INITIAL_STATUS_MUST_BE_CANDIDATE"):
        repo.save(published)


def test_repository_initialization_is_idempotent_and_does_not_mutate_legacy_table(tmp_path):
    db = tmp_path / "combined.db"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE quality_scenario(scenario_id TEXT PRIMARY KEY,name TEXT,status TEXT)"
        )
        connection.execute(
            "INSERT INTO quality_scenario VALUES('OLD-1','旧场景','PUBLISHED')"
        )

    SQLiteQualityScenarioV1Repository(db)
    SQLiteQualityScenarioV1Repository(db)

    with sqlite3.connect(db) as connection:
        legacy = connection.execute(
            "SELECT scenario_id,name,status FROM quality_scenario"
        ).fetchone()
        meta_count = connection.execute(
            "SELECT COUNT(*) FROM quality_scenario_v1_meta"
        ).fetchone()[0]
        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list('quality_scenario_v1')"
            ).fetchall()
        }
    assert legacy == ("OLD-1", "旧场景", "PUBLISHED")
    assert meta_count == 1
    assert "idx_qsv1_product_status" in indexes
    assert "idx_qsv1_lifecycle" in indexes
    assert "idx_qsv1_activity" in indexes
    assert "idx_qsv1_concern" in indexes

    with sqlite3.connect(db) as connection:
        qsv1_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('quality_scenario_v1')")
        }
    assert {
        "trigger_source",
        "trigger_reason",
        "quality_confirmed_by",
        "quality_confirmed_at",
        "technical_confirmed_by",
        "technical_confirmed_at",
        "confirmation_note",
    }.issubset(qsv1_columns)
