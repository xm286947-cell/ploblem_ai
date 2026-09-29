from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from compatibility.common_evidence import (
    CommonEvidenceContractError,
    build_overall_evidence_href,
)
from quality_knowledge.web.overall_shell import create_overall_shell_router


def _evidence() -> dict[str, object]:
    return {
        "contract_version": "common-evidence/v1.0",
        "evidence_id": "EV-R2-W3-001",
        "evidence_type": "TEXT",
        "source": {
            "source_type": "DOCUMENT",
            "source_id": "SRC-R2-W3",
            "source_version": "V3",
        },
        "locator": {
            "page": 7,
            "section": "Failure Analysis",
            "anchor": "root-cause",
        },
        "excerpt": "边界条件触发后通信恢复。",
        "source_text": "来源正文只通过公共 Evidence 投影展示。",
        "content_hash": "sha256:r2-w3",
        "source_ref": "SRC-R2-W3@V3",
        "source_reference": "https://example.test/source/SRC-R2-W3",
        "producer_domain": "Hardware Case",
        "producer_object_id": "HC-R2-W3",
        "producer_object_version": 3,
        "verification_status": "VERIFIED",
        "evidence_status": "ACTIVE",
        "created_at": None,
    }


def _client(provider=None) -> TestClient:
    app = FastAPI()
    app.include_router(create_overall_shell_router(task_provider=provider))
    return TestClient(app)


def test_w3_common_evidence_href_is_same_origin_contract_transport():
    href = build_overall_evidence_href(
        _evidence(),
        return_to="/p0/hardware-cases?filter=active#HC-R2-W3",
    )
    parsed = urlsplit(href)
    params = parse_qs(parsed.query)

    assert parsed.path == "/p0/overall/evidence"
    assert params["presentation"] == ["drawer"]
    assert params["return_to"] == ["/p0/hardware-cases?filter=active#HC-R2-W3"]
    assert "EV-R2-W3-001" in params["common_evidence"][0]

    try:
        build_overall_evidence_href(
            _evidence(),
            return_to="https://example.test/escape",
        )
    except CommonEvidenceContractError:
        pass
    else:
        raise AssertionError("external return target must fail closed")


def test_w3_evidence_viewer_projects_source_locator_excerpt_without_domain_read():
    client = _client()
    response = client.get(
        "/p0/overall/evidence",
        params={
            "presentation": "drawer",
            "common_evidence": json.dumps(_evidence(), ensure_ascii=False),
            "return_to": "/p0/hardware-cases?filter=active#HC-R2-W3",
        },
    )

    assert response.status_code == 200
    for marker in (
        "EV-R2-W3-001",
        "TEXT",
        "DOCUMENT",
        "SRC-R2-W3",
        "V3",
        "Failure Analysis",
        "root-cause",
        "边界条件触发后通信恢复",
        "来源正文只通过公共 Evidence 投影展示",
        "VERIFIED",
        "ACTIVE",
        "打开生产方来源",
        "Overall 不查询、不复制任何 Domain Evidence Store",
    ):
        assert marker in response.text
    assert 'href="/p0/hardware-cases?filter=active#HC-R2-W3"' in response.text


def test_w3_evidence_viewer_preserves_validated_return_state():
    client = _client()
    state = json.dumps(
        {
            "contract": "overall-return-context/v1",
            "scroll_y": 420,
            "selected_object": "HC-R2-W3",
            "fields": {"filter": "active"},
        },
        ensure_ascii=False,
    )
    response = client.get(
        "/p0/overall/evidence",
        params={
            "common_evidence": json.dumps(_evidence(), ensure_ascii=False),
            "return_to": "/p0/hardware-cases?filter=active#HC-R2-W3",
            "return_state": state,
        },
    )
    assert response.status_code == 200
    assert "overall_return_state=" in response.text
    assert "#HC-R2-W3" in response.text

    invalid = client.get(
        "/p0/overall/evidence",
        params={
            "return_to": "/p0/hardware-cases",
            "return_state": '{"contract":"overall-return-context/v1","unknown":1}',
        },
    )
    assert invalid.status_code == 400


def test_w3_task_provider_common_evidence_uses_public_viewer():
    client = _client(
        lambda: {
            "items": [
                {
                    "task_id": "T-R2-W3",
                    "title": "Evidence 回归",
                    "workspace_id": "hardware",
                    "status": "READY",
                    "common_evidence": _evidence(),
                    "return_to": "/p0/hardware-cases?tab=evidence",
                }
            ]
        }
    )
    page = client.get("/p0/overall")
    assert page.status_code == 200
    assert "presentation=drawer" in page.text
    assert "EV-R2-W3-001" in page.text


def test_w3_invalid_evidence_and_presentation_fail_closed():
    client = _client()
    assert client.get(
        "/p0/overall/evidence",
        params={"presentation": "popup"},
    ).status_code == 400

    broken = dict(_evidence())
    broken["contract_version"] = "common-evidence/v999"
    assert client.get(
        "/p0/overall/evidence",
        params={"common_evidence": json.dumps(broken)},
    ).status_code == 400

    assert client.get(
        "/p0/overall/evidence",
        params={
            "common_evidence": json.dumps(_evidence()),
            "return_to": "//evil.test/",
        },
    ).status_code == 400


def test_w3_drawer_runtime_is_same_origin_and_keeps_w2_return_contract():
    root = Path(__file__).resolve().parents[1]
    navigation = (
        root / "quality_knowledge/web/static/overall_navigation.js"
    ).read_text(encoding="utf-8")
    shell = (
        root / "quality_knowledge/web/templates/p0_base.html"
    ).read_text(encoding="utf-8")
    drawer = (
        root / "quality_knowledge/web/templates/overall_evidence_drawer.html"
    ).read_text(encoding="utf-8")

    assert "url.origin !== window.location.origin" in navigation
    assert "url.pathname !== '/p0/overall/evidence'" in navigation
    assert "presentation', 'drawer'" in navigation
    assert "return_state', JSON.stringify(capture())" in navigation
    assert "overall:evidence:return" in navigation
    assert "event.key === 'Escape'" in navigation
    assert "window.OverallNavigation = {capture, restoreNow, closeDrawer}" in navigation

    assert "overall_navigation.css?v=" in shell
    assert "overall_navigation.js?v=" in shell
    assert "data-overall-evidence-return" in drawer
    assert "window.parent.postMessage" in drawer
