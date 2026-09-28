from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
import json

from fastapi.testclient import TestClient

from compatibility.common_evidence import build_overall_evidence_href
from quality_knowledge.repositories import IssueKnowledgeRepository
from quality_knowledge.web import create_app
from quality_knowledge.web.overall_navigation import normalize_overall_return_state
from quality_knowledge.web.overall_shell import create_overall_shell_router
from quality_knowledge.web.p0_app import create_p0_app
from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.p04.fixtures import FixtureP04Provider


ROOT = Path(__file__).resolve().parents[1]


def _common_evidence() -> dict[str, object]:
    return {
        "contract_version": "common-evidence/v1.0",
        "evidence_id": "EV-TEST-01",
        "evidence_type": "TEXT",
        "source": {"source_type": "DOCUMENT", "source_id": "SRC-1", "source_version": "V2"},
        "locator": {"page": 4, "section": "测试章节", "anchor": "p4-a"},
        "excerpt": "可追溯的证据摘录",
        "source_text": "来源正文样例",
        "content_hash": "sha256:test",
        "source_ref": "SRC-1@V2",
        "source_reference": "https://example.test/source/SRC-1",
        "producer_domain": "Hardware Case",
        "producer_object_id": "HC-1",
        "producer_object_version": 3,
        "verification_status": "VERIFIED",
        "evidence_status": "ACTIVE",
        "created_at": None,
    }


def test_common_evidence_href_is_same_origin_and_contract_backed():
    href = build_overall_evidence_href(
        _common_evidence(), return_to="/p0/hardware-cases?filter=active#hc-1"
    )
    parsed = urlsplit(href)
    params = parse_qs(parsed.query)
    assert parsed.path == "/p0/overall/evidence"
    assert params["presentation"] == ["drawer"]
    assert params["return_to"] == ["/p0/hardware-cases?filter=active#hc-1"]
    assert "EV-TEST-01" in params["common_evidence"][0]
    try:
        build_overall_evidence_href(_common_evidence(), return_to="https://example.test/away")
    except ValueError:
        pass
    else:
        raise AssertionError("external return path must be rejected")


def test_common_evidence_viewer_renders_contract_and_drawer_mode():
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_overall_shell_router())
    client = TestClient(app)
    drawer = client.get(
        "/p0/overall/evidence",
        params={
            "presentation": "drawer",
            "common_evidence": json.dumps(_common_evidence(), ensure_ascii=False),
            "return_to": "/p0/hardware-cases",
        },
    )
    assert drawer.status_code == 200
    assert "EV-TEST-01" in drawer.text
    assert "可追溯的证据摘录" in drawer.text
    assert "来源正文样例" in drawer.text
    assert "打开生产方来源" in drawer.text
    assert "data-overall-evidence-viewer" in drawer.text
    assert client.get(
        "/p0/overall/evidence", params={"presentation": "popup"}
    ).status_code == 400


def test_task_provider_common_evidence_opens_the_shared_drawer():
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        create_overall_shell_router(
            task_provider=lambda: {
                "items": [
                    {
                        "task_id": "T-EVIDENCE",
                        "title": "证据回归样例",
                        "workspace_id": "hardware",
                        "status": "READY",
                        "common_evidence": _common_evidence(),
                        "return_to": "/p0/hardware-cases?tab=evidence",
                    }
                ]
            }
        )
    )
    page = TestClient(app).get("/p0/overall")
    assert page.status_code == 200
    assert 'presentation=drawer' in page.text
    assert 'EV-TEST-01' in page.text


def _seed_issue(repo: IssueKnowledgeRepository) -> None:
    with repo.connect() as connection:
        connection.execute(
            "INSERT INTO quality_issue(knowledge_id,business_type,business_issue_id,current_version_id,updated_at) VALUES(?,?,?,?,?)",
            ("QK-RETURN", "PLC", "ITR-RETURN", "QK-RETURN-V1", "2026-09-01 12:00:00"),
        )
        connection.execute(
            "INSERT INTO quality_issue_version(issue_version_id,knowledge_id,version_no,normalized_source_hash,title,month,normalized_json) VALUES(?,?,?,?,?,?,?)",
            ("QK-RETURN-V1", "QK-RETURN", 1, "hash-return", "Return test", "9月", "{}"),
        )


def test_legacy_issue_detail_carries_state_to_safe_return_target(tmp_path: Path):
    db = tmp_path / "quality.db"
    _seed_issue(IssueKnowledgeRepository(db))
    state = '{"contract":"overall-return-context/v1","scroll_y":340,"focus_id":"f","fields":{"filter":"open"}}'
    client = TestClient(create_app(db))

    response = client.get(
        "/issues/QK-RETURN",
        params={"return_to": "/itr/resolution-workbench?status=linked", "overall_return_state": state},
    )
    assert response.status_code == 200
    assert 'href="/itr/resolution-workbench?status=linked&amp;overall_return_state=' in response.text
    assert "返回工作台" in response.text

    assert client.get(
        "/issues/QK-RETURN", params={"return_to": "//evil.test/"}
    ).status_code == 400
    assert client.get(
        "/issues/QK-RETURN", params={"overall_return_state": "not-json"}
    ).status_code == 400


def test_p04_source_return_keeps_valid_cross_workspace_state(tmp_path: Path):
    db = tmp_path / "p0.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db)
    client = TestClient(
        create_p0_app(
            db,
            stage_runner=object(),
            p04_provider=FixtureP04Provider(),
            hardware_case_db_path=tmp_path / "hardware.sqlite3",
            hardware_tree_upload_dir=tmp_path / "tree_uploads",
            hardware_case_source_root=tmp_path / "sources",
        )
    )
    state = quote('{"contract":"overall-return-context/v1","scroll_y":725,"fields":{"filter":"industry"}}')
    response = client.get(
        "/p0/quality-scenario-sources/PROBLEM-003?return_to=/p0/quality-scenario-insights"
        f"&overall_return_state={state}"
    )
    assert response.status_code == 200
    assert "overall_return_state" in response.text


def test_return_context_validator_rejects_unknown_fields_and_accepts_contract():
    valid = '{"contract":"overall-return-context/v1","scroll_y":25,"fields":{}}'
    assert normalize_overall_return_state(valid)["scroll_y"] == 25
    try:
        normalize_overall_return_state('{"contract":"overall-return-context/v1","unknown":1}')
    except Exception as exc:
        assert "OVERALL_RETURN_CONTEXT_INVALID" in str(exc)
    else:
        raise AssertionError("unknown state fields must fail closed")
