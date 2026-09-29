from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.app import create_app as create_legacy_app
from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


def _host(tmp_path: Path) -> TestClient:
    p0_db = tmp_path / "p0.db"
    legacy_db = tmp_path / "legacy.db"
    P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    ).initialize(p0_db)
    create_legacy_app(legacy_db)
    return TestClient(
        create_p0_app(
            p0_db,
            stage_runner=object(),
            project_root=ROOT,
            legacy_quality_issue_db_path=legacy_db,
        )
    )


def _seed_issue(
    client: TestClient,
    tmp_path: Path,
    *,
    itr: str,
    missed_test: bool = False,
) -> str:
    source = tmp_path / (itr + ".xlsx")
    book = Workbook()
    sheet = book.active
    sheet.append(
        [
            "ITR 单号",
            "问题标题",
            "问题描述",
            "是否漏测",
            "产品",
            "平台",
            "严重程度",
        ]
    )
    sheet.append(
        [
            itr,
            "R2 关联恢复问题",
            "历史问题关联恢复专项",
            "是" if missed_test else "否",
            "PLC",
            "IDE",
            "A",
        ]
    )
    book.save(source)
    with source.open("rb") as stream:
        imported = client.post(
            "/api/issues/import",
            data={"business_type": "PLC"},
            files={"file": (source.name, stream)},
        )
    assert imported.status_code == 200, imported.text

    items = client.get("/api/issues", params={"limit": 50}).json()["items"]
    row = next(item for item in items if item["business_issue_id"] == itr)
    return row["knowledge_id"]


def test_r2_p0_resolution_and_assessment_use_legacy_problem_identity_and_real_links(
    tmp_path: Path,
) -> None:
    client = _host(tmp_path)
    itr = "ITR-R2-ASSOC-1"
    knowledge_id = _seed_issue(client, tmp_path, itr=itr)

    # The P0 store is intentionally isolated.  A legacy knowledge_id is not a
    # valid /api/v2 issue identity, which is why R2 workbench links must return
    # to the source-owning Existing Problem detail instead of /p0/issues.
    assert client.get(f"/api/v2/issues/{knowledge_id}").status_code == 404

    repository = client.app.state.legacy_quality_issue_services.material_repository

    resolution_group = repository.group("ITR-CS")
    repository.add_material(
        resolution_group,
        itr + "CS",
        {
            "问题信息_彻底解决单号": itr + "CS",
            "问题信息_问题描述": "彻底解决历史事实",
            "根因": "真实历史根因",
            "解决措施": "真实历史解决措施",
            "验证结果": "历史验证通过",
            "状态": "CLOSED",
        },
        "resolution-history.xlsx",
        "Resolution",
        2,
    )

    assessment_group = repository.group("SW-OPS")
    repository.add_material(
        assessment_group,
        itr + "CS",
        {
            "问题信息_彻底解决单号": itr + "CS",
            "问题信息_问题描述": "软件考核历史事实",
            "考核状态": "已完成",
            "考核结果": "历史考核结论",
            "责任部门": "研发部",
            "责任人": "历史责任人",
        },
        "assessment-history.xlsx",
        "Assessment",
        2,
    )
    repository.refresh_links()

    resolution = client.get("/p0/itr-resolution", params={"q": itr})
    assert resolution.status_code == 200
    assert "彻底解决历史事实" in resolution.text
    assert f"/issues/{knowledge_id}?" in resolution.text
    assert f"/p0/issues/{knowledge_id}?" not in resolution.text
    assert "return_to=%2Fp0%2Fitr-resolution%3Fq%3D" in resolution.text

    assessment = client.get("/p0/software-assessment", params={"q": itr})
    assert assessment.status_code == 200
    assert "软件考核历史事实" in assessment.text
    assert f"/issues/{knowledge_id}?" in assessment.text
    assert f"/p0/issues/{knowledge_id}?" not in assessment.text
    assert "return_to=%2Fp0%2Fsoftware-assessment%3Fq%3D" in assessment.text

    recovery = client.get("/p0/itr-recovery", params={"q": itr})
    assert recovery.status_code == 200
    assert itr in recovery.text
    assert f"/issues/{knowledge_id}?" in recovery.text
    assert f"/p0/issues/{knowledge_id}?" not in recovery.text
    assert "return_to=%2Fp0%2Fitr-recovery%3Fq%3D" in recovery.text

    detail = client.get(
        f"/issues/{knowledge_id}",
        params={"return_to": f"/p0/itr-resolution?q={itr}"},
    )
    assert detail.status_code == 200
    assert f'href="/p0/itr-resolution?q={itr}"' in detail.text
    assert 'data-problem-association="ITR"' in detail.text
    assert 'data-problem-association="RESOLUTION"' in detail.text
    assert 'data-problem-association="SOFTWARE_ASSESSMENT"' in detail.text
    # This issue is not marked as missed-test; do not guess the relation.
    assert 'data-problem-association="MISSED_TEST"' not in detail.text


def test_r2_p0_missed_test_keeps_historical_analysis_on_same_existing_problem(
    tmp_path: Path,
) -> None:
    client = _host(tmp_path)
    itr = "ITR-R2-MISSED-ASSOC-1"
    knowledge_id = _seed_issue(client, tmp_path, itr=itr, missed_test=True)

    workbench = client.get("/p0/missed-test-analysis", params={"q": itr})
    assert workbench.status_code == 200
    assert itr in workbench.text
    assert f"/issues/{knowledge_id}?" in workbench.text
    assert f"/p0/issues/{knowledge_id}?" not in workbench.text
    assert "return_to=%2Fp0%2Fmissed-test-analysis%3Fq%3D" in workbench.text

    detail = client.get(
        f"/issues/{knowledge_id}",
        params={"return_to": f"/p0/missed-test-analysis?q={itr}"},
    )
    assert detail.status_code == 200
    assert f'href="/p0/missed-test-analysis?q={itr}"' in detail.text
    assert 'data-problem-association="MISSED_TEST"' in detail.text

    rejected = client.get(
        f"/issues/{knowledge_id}",
        params={"return_to": "https://example.invalid/escape"},
    )
    assert rejected.status_code == 400
