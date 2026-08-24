from fastapi.testclient import TestClient

from quality_knowledge.product_config import ProductConfigRepository
from quality_knowledge.repositories import IssueKnowledgeRepository
from quality_knowledge.web import create_app
from quality_knowledge.web.statistics_presenter import present_common_gaps


def _seed_issue(repo, number, business_type="PLC"):
    knowledge_id = f"QK-{number:03d}"
    version_id = f"{knowledge_id}-V1"
    with repo.connect() as connection:
        connection.execute(
            "INSERT INTO quality_issue(knowledge_id,business_type,business_issue_id,current_version_id,updated_at) VALUES(?,?,?,?,?)",
            (knowledge_id, business_type, f"ISSUE-{number:03d}", version_id, f"2026-01-01 00:{number % 60:02d}:00"),
        )
        connection.execute(
            "INSERT INTO quality_issue_version(issue_version_id,knowledge_id,version_no,normalized_source_hash,title,product,normalized_json) VALUES(?,?,?,?,?,?,?)",
            (version_id, knowledge_id, 1, f"hash-{number}", f"Issue {number}", business_type, "{}"),
        )
    return knowledge_id, version_id


def _seed_gap(repo, knowledge_id, version_id, category="TEST_METHOD"):
    run_id = f"RUN-{knowledge_id}"
    with repo.connect() as connection:
        connection.execute(
            "INSERT INTO analysis_run(analysis_run_id,knowledge_id,issue_version_id,analysis_type,status) VALUES(?,?,?,?,?)",
            (run_id, knowledge_id, version_id, "capability_gap", "COMPLETED"),
        )
        connection.execute(
            "INSERT INTO issue_capability_gap(gap_id,analysis_run_id,knowledge_id,issue_version_id,dimension,category,description) VALUES(?,?,?,?,?,?,?)",
            (f"GAP-{knowledge_id}", run_id, knowledge_id, version_id, "TECHNICAL", category, "gap"),
        )


def test_issue_workspace_total_and_pagination_are_not_capped_at_100(tmp_path):
    db = tmp_path / "quality.db"
    repo = IssueKnowledgeRepository(db)
    for number in range(125):
        _seed_issue(repo, number)

    page = TestClient(create_app(db)).get("/issues?page=2&page_size=100")

    assert page.status_code == 200
    assert "<strong>125</strong>" in page.text
    assert "共 125 条，当前 101–125" in page.text
    assert "第 2 / 2 页" in page.text


def test_common_gap_drilldown_uses_semantic_filter_and_returns_every_match(tmp_path):
    db = tmp_path / "quality.db"
    repo = IssueKnowledgeRepository(db)
    for number in range(3):
        knowledge_id, version_id = _seed_issue(repo, number, "PLC" if number < 2 else "ROBOT")
        _seed_gap(repo, knowledge_id, version_id)
    client = TestClient(create_app(db))

    page = client.get("/issues?gap_dimension=TECHNICAL&gap_category=TEST_METHOD")

    assert page.status_code == 200
    assert page.text.count('class="issue-id"') == 3
    assert "ISSUE-000" in page.text and "ISSUE-002" in page.text


def test_statistics_uses_dynamic_products_and_keeps_cross_product_breadth(tmp_path):
    db = tmp_path / "quality.db"
    repo = IssueKnowledgeRepository(db)
    first = _seed_issue(repo, 1, "PLC")
    second = _seed_issue(repo, 2, "ROBOT")
    _seed_gap(repo, *first)
    _seed_gap(repo, *second)
    ProductConfigRepository(db).upsert("ROBOT", "工业机器人", "ROBOT", "EMBEDDED", True, 8)

    rows = repo.aggregate_common_capability_gaps(business_type="PLC", min_issues=2)
    page = TestClient(create_app(db)).get("/statistics?business_type=PLC")

    assert rows[0]["business_type_count"] == 2
    assert "工业机器人（ROBOT）" in page.text
    assert "跨产品共性" in page.text


def test_presenter_uses_knowledge_id_for_kpi_and_semantic_drilldown():
    view = present_common_gaps([{
        "dimension": "TECHNICAL", "category": "TEST_METHOD", "related_issue_count": 2,
        "business_type_count": 2, "business_types": "PLC,ROBOT",
        "related_issues": "DUPLICATE,DUPLICATE", "related_knowledge_ids": "QK-1,QK-2",
    }])

    assert view["summary"]["related_issue_count"] == 2
    assert "gap_dimension=TECHNICAL" in view["items"][0]["drilldown_url"]
    assert "gap_category=TEST_METHOD" in view["items"][0]["drilldown_url"]
