from pathlib import Path

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.p0.repository import P0Repository


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_repository(tmp_path):
    db_path = tmp_path / "p0.db"
    P0Initializer(
        manifest_path=PROJECT_ROOT / "quality_knowledge" / "config" / "p0_seed_manifest.json",
        plc_seed_path=PROJECT_ROOT / "quality_knowledge" / "config" / "plc_fields.yaml",
    ).initialize(db_path)
    return P0Repository(db_path)


def save_issue(repository):
    return repository.save_issue(
        knowledge_id="K-1",
        business_issue_id="PLC-1",
        raw_json={"ITR单号": "PLC-1", "问题描述": "原始事实", "SourceID": "feishu-row-123"},
        normalized_snapshot={"ISSUE_FACT": {"business_issue_id": "PLC-1", "description": "标准化事实"}},
        mapping_config_id="MAP-PLC-V1",
        mapping_config_version=1,
        standard_catalog_version_id="SFC-PLC-V1",
        source_file_sha256="file-hash",
        sheet_name="PLC",
        row_number=2,
    )


def test_issue_save_is_idempotent_and_preserves_raw_and_snapshot(tmp_path):
    repository = make_repository(tmp_path)
    first = save_issue(repository)
    second = save_issue(repository)
    issue = repository.get_issue("K-1")
    assert first["outcome"] == "APPLIED"
    assert second == {"outcome": "ALREADY_APPLIED", "issue_version_id": first["issue_version_id"]}
    assert issue["raw_json"]["问题描述"] == "原始事实"
    assert issue["raw_json"]["SourceID"] == "feishu-row-123"
    assert issue["normalized_snapshot"]["ISSUE_FACT"]["description"] == "标准化事实"


def test_analysis_save_projects_v2_and_is_idempotent(tmp_path):
    repository = make_repository(tmp_path)
    issue = save_issue(repository)
    analysis = {
        "analysis_set_id": "AS-1",
        "knowledge_id": "K-1",
        "issue_version_id": issue["issue_version_id"],
        "taxonomy_version_id": "QUALITY_TAXONOMY_P0_V1",
        "input_hash": "analysis-input-hash",
        "stages": [{"stage": "occurrence", "status": "COMPLETED"}],
        "tags": [{"stage": "occurrence", "axis": "DOMAIN", "tag_code": "SOFTWARE", "confidence": 0.6}],
        "mrc": [{"side": "OCCURRENCE", "mrc_code": "DESIGN_REVIEW_INEFFECTIVE", "confidence": 0.6}],
        "capability_gaps": [{"capability_axis": "QUALITY_ENGINEERING", "capability_code": "TEST_VERIFICATION", "confidence": 0.6}],
        "evidence": [{"stage": "occurrence", "source_ref": "ISR", "excerpt": "原始行", "confidence": 0.6}],
    }
    assert repository.save_analysis_set(analysis)["outcome"] == "APPLIED"
    assert repository.save_analysis_set({**analysis, "analysis_set_id": "AS-DUP"}) == {
        "outcome": "ALREADY_APPLIED", "analysis_set_id": "AS-1"
    }
    saved = repository.get_analysis_set("AS-1")
    assert saved["tags"][0]["tag_code"] == "SOFTWARE"
    assert saved["mrc"][0]["mrc_code"] == "DESIGN_REVIEW_INEFFECTIVE"
    assert saved["capability_gaps"][0]["capability_axis"] == "QUALITY_ENGINEERING"


def test_human_revision_is_versioned_and_never_overwrites_ai_or_source(tmp_path):
    repository = make_repository(tmp_path)
    issue = save_issue(repository)
    analysis = {
        "analysis_set_id": "AS-1",
        "knowledge_id": "K-1",
        "issue_version_id": issue["issue_version_id"],
        "taxonomy_version_id": "QUALITY_TAXONOMY_P0_V1",
        "input_hash": "analysis-input-hash",
        "mrc": [{"side": "OCCURRENCE", "mrc_code": "DESIGN_REVIEW_INEFFECTIVE", "confidence": 0.6}],
    }
    repository.save_analysis_set(analysis)
    revision = repository.save_human_revision(
        base_analysis_set_id="AS-1",
        base_input_hash="analysis-input-hash",
        confirmed_by="quality-owner",
        answers=[{
            "target_path": "occurrence.mrc.primary",
            "original_value": "DESIGN_REVIEW_INEFFECTIVE",
            "confirmed_value": "CHANGE_IMPACT_NOT_ASSESSED",
            "changes_insight": True,
        }],
    )
    assert revision["revision_no"] == 1
    assert repository.get_analysis_set("AS-1")["mrc"][0]["mrc_code"] == "DESIGN_REVIEW_INEFFECTIVE"
    assert repository.get_issue("K-1")["raw_json"]["问题描述"] == "原始事实"
    assert repository.get_human_revisions("AS-1")[0]["answers"][0]["confirmed_value_json"] == "CHANGE_IMPACT_NOT_ASSESSED"
