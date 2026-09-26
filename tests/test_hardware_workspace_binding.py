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


def test_hardware_workspace_and_case_deep_link_bind_to_existing_overall_shell(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    workspace = client.get("/p0/hardware-cases")
    assert workspace.status_code == 200
    assert 'href="/p0/issues">问题工作台</a>' in workspace.text
    assert 'href="/p0/hardware-cases">硬件案例库</a>' in workspace.text
    assert 'href="/p0/issues">返回总体工作台</a>' in workspace.text

    detail = client.get("/p0/hardware-cases/HC-OFI-04-DEEP-LINK")
    assert detail.status_code == 200
    assert "HC-OFI-04-DEEP-LINK" in detail.text
    assert 'href="/p0/hardware-cases">案例首页</a>' in detail.text
    assert 'href="/p0/issues">返回总体工作台</a>' in detail.text

    # The Hardware Case workspace remains mounted on the existing app/API.
    assert client.get("/api/v2/hardware-cases").status_code == 200


def test_hardware_domain_host_keeps_existing_case_workspace_routes(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    for path in (
        "/p0/hardware-cases",
        "/p0/hardware-cases/tree",
        "/p0/hardware-cases/search",
        "/p0/hardware-cases/HC-OFI-04-DIRECT-ACCESS",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert 'href="/p0/issues">返回总体工作台</a>' in response.text
