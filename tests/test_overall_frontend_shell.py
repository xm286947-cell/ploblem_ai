from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.overall_shell import create_overall_shell_router
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


def _task_provider():
    return {
        "items": [
            {
                "task_id": "T-MAJOR-1",
                "title": "复核 Repeat Risk",
                "workspace_id": "major",
                "status": "READY",
                "deep_link": "/p0/cases",
                "evidence_link": "/p0/overall/evidence?producer_domain=Major",
                "return_to": "/p0/overall",
            }
        ]
    }


def test_overall_shell_has_four_stable_workspace_entry_points():
    app = FastAPI()
    app.include_router(create_overall_shell_router(task_provider=_task_provider))
    client = TestClient(app)

    page = client.get("/p0/overall")
    assert page.status_code == 200
    for marker in (
        "重大问题案例库 × Repeat Risk",
        "质量场景库",
        "硬件案例库",
        "存储器件寿命智能产品",
    ):
        assert marker in page.text

    workspaces = client.get("/api/v2/overall/workspaces").json()
    assert workspaces["total"] == 4
    expected = {
        "major": "/p0/cases",
        "quality-scenario": "/p0/quality-scenario-insights",
        "hardware": "/p0/hardware-cases",
        "storage": "/storage-workspace/",
    }
    for workspace_id, target in expected.items():
        response = client.get(
            f"/p0/workspaces/{workspace_id}",
            follow_redirects=False,
        )
        assert response.status_code in {302, 307}
        assert response.headers["location"] == target


def test_task_overview_is_provider_driven_and_never_requires_domain_repository():
    app = FastAPI()
    app.include_router(create_overall_shell_router(task_provider=_task_provider))
    client = TestClient(app)

    result = client.get("/api/v2/overall/task-overview").json()
    assert result["state"] == "READY"
    assert result["total"] == 1
    assert result["items"][0]["deep_link"] == "/p0/cases"
    assert result["items"][0]["return_to"] == "/p0/overall"

    empty = FastAPI()
    empty.include_router(create_overall_shell_router())
    empty_result = TestClient(empty).get("/api/v2/overall/task-overview").json()
    assert empty_result["state"] == "NO_PROVIDER"
    assert empty_result["items"] == []
    assert "不直接读取任何 Domain Repository" in empty_result["message"]


def test_common_evidence_navigation_and_return_framework_fail_closed():
    app = FastAPI()
    app.include_router(create_overall_shell_router())
    client = TestClient(app)

    evidence = client.get(
        "/p0/overall/evidence",
        params={
            "producer_domain": "Hardware Case",
            "evidence_id": "EV-001",
            "producer_object_id": "HC-001",
            "source_ref": "DOC-1@v1",
            "object_href": "/p0/hardware-cases/HC-001",
            "return_to": "/p0/hardware-cases",
        },
    )
    assert evidence.status_code == 200
    assert "common-evidence/v1.0" in evidence.text
    assert "EV-001" in evidence.text
    assert 'href="/p0/hardware-cases/HC-001"' in evidence.text
    assert 'href="/p0/hardware-cases"' in evidence.text

    returned = client.get(
        "/p0/overall/return",
        params={"to": "/p0/overall"},
        follow_redirects=False,
    )
    assert returned.status_code in {302, 307}
    assert returned.headers["location"] == "/p0/overall"

    assert client.get(
        "/p0/overall/return",
        params={"to": "https://example.com"},
        follow_redirects=False,
    ).status_code == 400
    assert client.get(
        "/p0/overall/return",
        params={"to": "//example.com"},
        follow_redirects=False,
    ).status_code == 400


def test_unified_p0_host_mounts_overall_shell_without_breaking_direct_domain_entries(tmp_path):
    db_path = tmp_path / "overall.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db_path)

    app = create_p0_app(
        db_path,
        stage_runner=object(),
        hardware_case_db_path=tmp_path / "hardware.db",
        hardware_tree_upload_dir=tmp_path / "tree",
        hardware_case_source_root=tmp_path / "sources",
        overall_task_provider=_task_provider,
    )
    client = TestClient(app)

    assert app.state.overall_shell_enabled is True
    assert client.get("/p0/overall").status_code == 200
    assert client.get("/p0/cases").status_code == 200
    assert client.get("/p0/quality-scenario-insights").status_code == 200
    assert client.get("/p0/hardware-cases").status_code == 200

    # OFI-01 provides the stable Storage shell entry only.  OFI-05 owns the
    # actual /p0/storage Domain binding.
    storage = client.get("/p0/workspaces/storage", follow_redirects=False)
    assert storage.headers["location"] == "/storage-workspace/"


def test_hardware_only_direct_entry_remains_compatible_and_no_second_web_stack(tmp_path):
    app = create_p0_app(
        tmp_path / "unused-quality.db",
        hardware_case_db_path=tmp_path / "hardware.db",
        hardware_tree_upload_dir=tmp_path / "tree",
        hardware_case_source_root=tmp_path / "sources",
        enabled_domains={"HARDWARE_CASE"},
    )
    client = TestClient(app)

    assert app.state.overall_shell_enabled is False
    assert client.get("/p0/hardware-cases").status_code == 200
    assert client.get("/p0/overall").status_code == 404

    source = (ROOT / "quality_knowledge/web/overall_shell.py").read_text(encoding="utf-8")
    assert "FastAPI(" not in source
