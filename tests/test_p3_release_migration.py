import json
import sqlite3

import pytest

from quality_knowledge.human_analysis import HumanAnalysisRepository
from quality_knowledge.release_migration import ReleaseMigration
from quality_knowledge.repositories import IssueKnowledgeRepository


def _historical_db(path):
    IssueKnowledgeRepository(path)
    human = HumanAnalysisRepository(path)
    field = human.create_field("review", "人工结论", "TEXT")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO quality_issue VALUES(?,?,?,?,?,?,?)", (
            "QK-HMI-1", "HMI", "HMI-OLD-1", "QK-HMI-1-V1", "ACTIVE", "2025-01-01", "2025-01-01"))
        conn.execute("""INSERT INTO quality_issue_version(
            issue_version_id,knowledge_id,version_no,normalized_source_hash,title,normalized_json)
            VALUES(?,?,?,?,?,?)""", ("QK-HMI-1-V1", "QK-HMI-1", 1, "old-hash", "历史问题", "{}"))
    human.save_values("QK-HMI-1", "QK-HMI-1-V1", {field["field_id"]: "保留"}, "tester")


def test_dry_run_report_then_apply_preserves_history_and_audit(tmp_path):
    source, target, report = tmp_path / "old.db", tmp_path / "p3.db", tmp_path / "migration.json"
    _historical_db(source)
    migration = ReleaseMigration()
    dry = migration.dry_run(source, target, report)
    assert dry["can_apply"] is True
    assert dry["data_groups"]["knowledge"] >= 2
    assert dry["data_groups"]["human_analysis"] >= 3
    assert not target.exists()

    result = migration.apply(report)
    assert result["migration_result"] == "APPLIED"
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT title FROM quality_issue_version").fetchone()[0] == "历史问题"
        assert json.loads(conn.execute("SELECT value_json FROM human_analysis_value").fetchone()[0]) == "保留"
        assert conn.execute("SELECT COUNT(*) FROM release_migration_audit").fetchone()[0] == 1


def test_apply_rejects_changed_source_and_existing_target(tmp_path):
    source, target, report = tmp_path / "old.db", tmp_path / "p3.db", tmp_path / "migration.json"
    _historical_db(source)
    migration = ReleaseMigration()
    migration.dry_run(source, target, report)
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE quality_issue SET status='ARCHIVED'")
    with pytest.raises(ValueError, match="SOURCE_CHANGED_AFTER_DRY_RUN"):
        migration.apply(report)

    target.write_bytes(b"occupied")
    dry = migration.dry_run(source, target, report)
    assert dry["can_apply"] is False and "TARGET_ALREADY_EXISTS" in dry["conflicts"]


def test_dry_run_never_mutates_source(tmp_path):
    source, target, report = tmp_path / "old.db", tmp_path / "p3.db", tmp_path / "migration.json"
    _historical_db(source)
    before = source.read_bytes()
    ReleaseMigration().dry_run(source, target, report)
    assert source.read_bytes() == before
    assert not target.exists()
