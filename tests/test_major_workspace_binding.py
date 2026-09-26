from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


def _client(tmp_path: Path) -> TestClient:
    p0_db = tmp_path / "quality_capability_p0.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(p0_db)
    return TestClient(
        create_p0_app(
            p0_db,
            stage_runner=object(),
            hardware_case_db_path=tmp_path / "hardware_case.sqlite3",
            hardware_tree_upload_dir=tmp_path / "tree_uploads",
            hardware_case_source_root=tmp_path / "sources",
        )
    )


def test_major_repeat_workspace_and_case_deep_link_bind_to_existing_overall_shell(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    issue = client.get("/p0/issues/OFI-02-ITR")
    assert issue.status_code == 200
    assert "Repeat Risk" in issue.text
    assert 'href="/p0/cases">打开重大问题案例库</a>' in issue.text
    assert 'href="/p0/issues">← 返回问题工作台</a>' in issue.text

    workspace = client.get("/p0/cases")
    assert workspace.status_code == 200
    assert 'href="/p0/cases">重大问题案例库</a>' in workspace.text
    assert 'href="/p0/issues">返回总体工作台</a>' in workspace.text

    detail = client.get("/p0/cases/CASE-OFI-02-DEEP-LINK")
    assert detail.status_code == 200
    assert 'href="/p0/cases">← 返回重大问题案例库</a>' in detail.text
    assert 'href="/p0/issues">返回总体工作台</a>' in detail.text
    assert 'id="case-evidence"' in detail.text


def test_major_repeat_direct_routes_keep_public_contract_boundary(tmp_path: Path) -> None:
    client = _client(tmp_path)

    for path in (
        "/p0/issues",
        "/p0/issues/OFI-02-DIRECT",
        "/p0/cases",
        "/p0/cases/CASE-OFI-02-DIRECT",
    ):
        response = client.get(path)
        assert response.status_code == 200

    openapi_paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v2/issues/{knowledge_id}/repeat-risk" in openapi_paths
    assert "/api/v2/issues/{knowledge_id}/repeat-risk/queries" in openapi_paths
    assert "/api/v2/historical-cases" in openapi_paths
    assert "/api/v2/historical-cases/{case_id}" in openapi_paths

    exposed = "\n".join(openapi_paths).lower()
    assert "repository" not in exposed
    assert "sqlite" not in exposed
