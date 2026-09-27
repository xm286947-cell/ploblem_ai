from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


def _storage_stub() -> FastAPI:
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    def home():
        return '<a id="overallShellBack" href="/p0/overall">返回 Overall Shell</a>'

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "storage-life-parent-smoke"}

    return app


def _client(tmp_path: Path) -> TestClient:
    db = tmp_path / "parent-overall.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(db)
    app = create_p0_app(
        db,
        stage_runner=object(),
        hardware_case_db_path=tmp_path / "hardware.db",
        hardware_tree_upload_dir=tmp_path / "tree",
        hardware_case_source_root=tmp_path / "sources",
        storage_app=_storage_stub(),
    )
    return TestClient(app)


def test_parent_overall_shell_converges_all_four_workspace_entries(tmp_path):
    client = _client(tmp_path)

    overall = client.get("/p0/overall")
    assert overall.status_code == 200
    for marker in (
        "重大问题案例库 × Repeat Risk",
        "质量场景库",
        "硬件案例库",
        "存储器件寿命智能产品",
    ):
        assert marker in overall.text

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


def test_current_problem_workspace_keeps_canonical_routes_and_overall_return(tmp_path):
    client = _client(tmp_path)

    root = client.get("/", follow_redirects=False)
    assert root.status_code in {302, 307}
    assert root.headers["location"] == "/p0/issues"

    overall = client.get("/p0/overall")
    assert overall.status_code == 200
    assert 'href="/p0/issues">进入问题工作台</a>' in overall.text

    workspace = client.get("/p0/issues")
    assert workspace.status_code == 200
    assert 'href="/p0/overall">返回总体工作台</a>' in workspace.text

    issues = client.get("/api/v2/issues")
    assert issues.status_code == 200
    assert isinstance(issues.json().get("items"), list)


def test_overall_shell_exposes_existing_capability_routes(tmp_path):
    client = _client(tmp_path)
    page = client.get("/p0/overall")

    assert page.status_code == 200
    for path, label in (
        ("/p0/issues", "当前问题"),
        ("/p0/batch-analysis", "批量 AI 分析"),
        ("/p0/insights", "质量洞察"),
        ("/p0/quality-scenario-insights", "质量画像与洞察"),
        ("/p1/product-reports", "产品综合报告"),
        ("/p1/risk-assessment", "正向风险评估（P1）"),
        ("/p0/data-intake", "数据接入"),
        ("/p0/settings", "字段映射与产品"),
    ):
        assert f'href="{path}">{label}</a>' in page.text
        assert client.get(path).status_code == 200


def test_case_and_knowledge_navigation_uses_existing_product_pages(tmp_path):
    client = _client(tmp_path)
    page = client.get("/p0/overall")

    assert '<section class="overall-card" id="case-knowledge"' in page.text
    for path, label in (
        ("/p0/cases", "重大问题案例库"),
        ("/p0/hardware-cases", "硬件案例库"),
        ("/storage-workspace/knowledge-production/published", "已发布知识"),
        ("/storage-workspace/knowledge-production/sources", "统一知识生产"),
    ):
        assert f'href="{path}"><strong>{label}</strong>' in page.text


def test_vnext_product_areas_cover_frozen_information_architecture(tmp_path):
    client = _client(tmp_path)
    expected = {
        "current-problem": ("当前问题", "/p0/issues"),
        "cases-knowledge": ("案例与知识", "/p0/cases"),
        "scenarios-insights": ("质量场景与洞察", "/p0/quality-scenario-insights?view=PRODUCT"),
        "professional-topics": ("专业专题", "/storage-workspace/"),
        "management": ("管理与配置", "/p0/data-intake"),
    }

    for area_id, (title, capability_path) in expected.items():
        page = client.get(f"/p0/overall/areas/{area_id}")
        assert page.status_code == 200
        assert f"<h1>{title}</h1>" in page.text
        assert f'href="{capability_path}"' in page.text

    current = client.get("/p0/overall/areas/current-problem")
    assert "Repeat Risk 保持在当前问题详情和彻底解决流程中" in current.text
    assert "当前运行环境未绑定 Legacy 数据库" in current.text

    missing = client.get("/p0/overall/areas/not-found")
    assert missing.status_code == 404


def test_parent_workspace_return_paths_all_target_canonical_overall_shell(tmp_path):
    client = _client(tmp_path)

    major = client.get("/p0/cases")
    qs = client.get("/p0/quality-scenario-insights")
    hardware = client.get("/p0/hardware-cases")
    storage = client.get("/storage-workspace/")

    for response in (major, qs, hardware, storage):
        assert response.status_code == 200
        assert 'href="/p0/overall"' in response.text


def test_parent_common_evidence_and_return_framework_are_same_origin(tmp_path):
    client = _client(tmp_path)

    evidence = client.get(
        "/p0/overall/evidence",
        params={
            "producer_domain": "Hardware Case",
            "evidence_id": "EV-PARENT-1",
            "producer_object_id": "HC-PARENT-1",
            "object_href": "/p0/hardware-cases/HC-PARENT-1",
            "return_to": "/p0/hardware-cases",
        },
    )
    assert evidence.status_code == 200
    assert "common-evidence/v1.0" in evidence.text
    assert 'href="/p0/hardware-cases/HC-PARENT-1"' in evidence.text
    assert 'href="/p0/hardware-cases"' in evidence.text

    ok = client.get("/p0/overall/return", params={"to": "/p0/overall"}, follow_redirects=False)
    assert ok.status_code in {302, 307}
    assert ok.headers["location"] == "/p0/overall"

    assert client.get(
        "/p0/overall/return",
        params={"to": "https://example.com"},
        follow_redirects=False,
    ).status_code == 400


def test_parent_boundary_has_no_direct_domain_repository_or_second_web_stack():
    source = (ROOT / "quality_knowledge/web/overall_shell.py").read_text(encoding="utf-8")
    storage_binding = (ROOT / "quality_knowledge/web/storage_workspace.py").read_text(encoding="utf-8")

    assert "FastAPI(" not in source
    assert "Repository(" not in source
    assert "sqlite" not in source.lower()
    assert ".mount(" in storage_binding
    assert "FastAPI(" not in storage_binding
