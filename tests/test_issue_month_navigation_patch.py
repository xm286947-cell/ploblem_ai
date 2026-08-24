from fastapi.testclient import TestClient

from quality_knowledge.repositories import IssueKnowledgeRepository
from quality_knowledge.web import create_app


def _seed_issue(repo, knowledge_id, issue_id, month, updated_at):
    version_id = f"{knowledge_id}-V1"
    with repo.connect() as connection:
        connection.execute(
            "INSERT INTO quality_issue(knowledge_id,business_type,business_issue_id,current_version_id,updated_at) VALUES(?,?,?,?,?)",
            (knowledge_id, "PLC", issue_id, version_id, updated_at),
        )
        connection.execute(
            "INSERT INTO quality_issue_version(issue_version_id,knowledge_id,version_no,normalized_source_hash,title,month,normalized_json) VALUES(?,?,?,?,?,?,?)",
            (version_id, knowledge_id, 1, f"hash-{knowledge_id}", issue_id, month, "{}"),
        )


def test_issue_workspace_displays_and_filters_month(tmp_path):
    db = tmp_path / "quality.db"
    repo = IssueKnowledgeRepository(db)
    _seed_issue(repo, "QK-NEW", "ITR-NEW", "2月", "2026-02-02 12:00:00")
    _seed_issue(repo, "QK-OLD", "ITR-OLD", "1月", "2026-01-01 12:00:00")
    client = TestClient(create_app(db))

    page = client.get("/issues?month=2%E6%9C%88")

    assert page.status_code == 200
    assert "全部月份" in page.text
    assert "ITR-NEW" in page.text
    assert "ITR-OLD" not in page.text


def test_issue_detail_has_previous_and_next_navigation(tmp_path):
    db = tmp_path / "quality.db"
    repo = IssueKnowledgeRepository(db)
    _seed_issue(repo, "QK-NEW", "ITR-NEW", "2月", "2026-02-02 12:00:00")
    _seed_issue(repo, "QK-OLD", "ITR-OLD", "1月", "2026-01-01 12:00:00")
    client = TestClient(create_app(db))

    first = client.get("/issues/QK-NEW")
    second = client.get("/issues/QK-OLD")

    assert first.status_code == 200
    assert 'href="/issues/QK-OLD"' in first.text
    assert "1 / 2" in first.text
    assert second.status_code == 200
    assert 'href="/issues/QK-NEW"' in second.text
    assert "2 / 2" in second.text
