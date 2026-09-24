from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("runtime")

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from mock_openai import running_openai_mock

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.reverse_quality_runtime import ReverseQualityRuntimeExecutor
from quality_knowledge.web.app import create_app
from quality_knowledge.web.p0_app import create_p0_app


FIXTURES = HERE / "mock_ai_fixtures.json"
AGENT_CONFIG = ROOT / "config/runtime/agents/reverse_quality.single_issue.analyze.yaml"
SECRET = "QS_PRODUCT_TEST_ONLY_SECRET"


def _fixtures() -> dict:
    payload = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert payload["classification"] == "SANITIZED_SYNTHETIC"
    assert payload["contains_internal_real_data"] is False
    return payload["cases"]


def _model_config(tmp_path: Path, base_url: str) -> Path:
    path = tmp_path / "model.local.yaml"
    path.write_text(
        f"""
models:
  qwen_prod:
    provider: openai_compatible
    base_url: {base_url}
    api_key: {SECRET}
    model: mock-gpt
    temperature: 0
    max_tokens: 8192
""".strip(),
        encoding="utf-8",
    )
    return path


def _executor(tmp_path: Path, base_url: str) -> ReverseQualityRuntimeExecutor:
    return ReverseQualityRuntimeExecutor(
        ROOT,
        tmp_path / "runtime.db",
        model_config_path=_model_config(tmp_path, base_url),
        agent_config_path=AGENT_CONFIG,
        environ={},
    )


def _runtime_bytes(tmp_path: Path) -> bytes:
    return b"".join(
        path.read_bytes()
        for path in sorted(tmp_path.glob("runtime.db*"))
        if path.is_file()
    )


def _initializer() -> P0Initializer:
    return P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    )


def _p0_client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "quality_scenario_product_test.db"
    _initializer().initialize(db_path)
    return TestClient(create_p0_app(db_path, stage_runner=None))


def _taxonomy() -> dict:
    return {
        "version_id": "STV-QS-PRODUCT-TEST",
        "lifecycles": [
            {
                "lifecycle_code": "RUNTIME_EXECUTION",
                "label_zh": "运行执行",
                "enabled": 1,
            }
        ],
        "activities": [
            {
                "activity_code": "POWER_LOSS_RETENTION_RECOVERY",
                "lifecycle_code": "RUNTIME_EXECUTION",
                "label_zh": "掉电数据保持与上电恢复",
                "objective": "保证掉电后关键数据正确恢复",
                "chain_text": "运行 → 掉电 → 上电 → 恢复",
                "enabled": 1,
            }
        ],
    }


def _reverse_result(*, missing=None, canonical_itr="ITR-QS-PT-001") -> dict:
    return {
        "result_version": "reverse-quality-v0.1",
        "analysis_id": "RQA-QS-PT-001",
        "run_id": "RQRUN-QS-PT-001",
        "run_seq": 1,
        "identity": {
            "canonical_itr": canonical_itr,
            "product_code": "PLC",
            "taxonomy_version_id": "STV-QS-PRODUCT-TEST",
        },
        "fields": {
            "lifecycle_stage": {
                "value": "运行执行",
                "source_type": "FACT",
                "evidence_ids": ["cs.phase"],
                "confidence": 0.9,
                "review_status": "CONFIRMED",
            },
            "business_activity_scene": {
                "value": "掉电数据保持与上电恢复",
                "source_type": "FACT",
                "evidence_ids": ["cs.description"],
                "confidence": 0.9,
                "review_status": "CONFIRMED",
            },
            "customer_experience": {
                "value": "掉电后关键计数丢失",
                "source_type": "FACT",
                "evidence_ids": ["cs.description"],
                "confidence": 0.9,
                "review_status": "CONFIRMED",
            },
            "quality_risk": {
                "value": "数据完整性",
                "source_type": "INFERRED",
                "evidence_ids": ["cs.description"],
                "confidence": 0.7,
                "review_status": "PENDING",
            },
            "expected_quality_state": {
                "value": "重新上电后计数正确恢复",
                "source_type": "INFERRED",
                "evidence_ids": ["cs.description"],
                "confidence": 0.8,
                "review_status": "PENDING",
            },
            "trigger_condition": {
                "value": "运行中异常掉电",
                "source_type": "FACT",
                "evidence_ids": ["cs.description"],
                "confidence": 0.9,
                "review_status": "CONFIRMED",
            },
        },
        "missing_information": missing or [],
    }


def _create_candidate(
    client: TestClient,
    *,
    reverse_result: dict | None = None,
    trigger_source: str = "HIGH_PERCEPTION",
    trigger_reason: str = "客户高感知问题进入场景深挖",
) -> dict:
    response = client.post(
        "/api/v2/quality-scenarios/candidates/from-reverse",
        json={
            "reverse_quality_result": reverse_result or _reverse_result(),
            "taxonomy": _taxonomy(),
            "trigger_source": trigger_source,
            "trigger_reason": trigger_reason,
            "created_by": "PRODUCT_TEST_CENTER",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["scenario"]


def _review_confirm_publish(client: TestClient, scenario: dict) -> dict:
    reviewed = client.post(
        f"/api/v2/quality-scenarios/{scenario['scenario_id']}/review",
        json={
            "expected_scenario_version": scenario["scenario_version"],
            "patch": {},
            "review_status": "CONFIRMED",
            "reviewer": "QUALITY_OWNER_TEST",
            "comment": "产品测试：Evidence核对完成",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    reviewed_scenario = reviewed.json()["scenario"]

    confirmed = client.post(
        f"/api/v2/quality-scenarios/{scenario['scenario_id']}/confirm",
        json={
            "expected_scenario_version": reviewed_scenario["scenario_version"],
            "quality_confirmed_by": "QUALITY_OWNER_TEST",
            "technical_confirmed_by": "RND_OWNER_TEST",
            "confirmation_note": "专业质量确认场景事实；研发确认技术判断。",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    confirmed_scenario = confirmed.json()["scenario"]

    published = client.post(
        f"/api/v2/quality-scenarios/{scenario['scenario_id']}/publish",
        json={
            "expected_scenario_version": confirmed_scenario["scenario_version"],
            "published_by": "QUALITY_OWNER_TEST",
        },
    )
    assert published.status_code == 200, published.text
    return published.json()["scenario"]


def _analyse_with_mock(tmp_path: Path, case_name: str):
    case = _fixtures()[case_name]
    with running_openai_mock(case) as (server, provider):
        host, port = server.server_address
        legacy_app = create_app(tmp_path / f"reverse_{case_name}.db")
        reverse_service = legacy_app.state.reverse_quality_service
        reverse_service.ai_client = None
        reverse_service._runtime_executor = _executor(
            tmp_path,
            f"http://{host}:{port}/v1",
        )
        material_repo = legacy_app.state.material_repository
        material_id, _ = material_repo.add_material(
            material_repo.group("ITR-CS"),
            f"ITR20260924{case_name[:4].upper()}",
            {
                "问题信息_问题描述": "PLC 正常运行时异常掉电，重新上电后关键计数丢失",
                "问题信息_问题原因定位": "保持变量写入未完成",
                "问题信息_问题发生阶段": "终端正常使用",
                "问题信息_产品型号": "PLC AM600",
                "问题信息_问题领域": "软件",
                "问题处理结果_问题解决方案": "重新上电恢复运行",
            },
            "sanitized_product_test.xlsx",
            "Sheet1",
            2,
        )
        result = reverse_service.analyse(material_id, "PLC")
        taxonomy = legacy_app.state.scenario_repository.taxonomy_active("PLC")
        return result, taxonomy, provider


def test_qs_pt_001_openai_mock_contract_and_runtime_secret_safety(tmp_path):
    reverse, _, provider = _analyse_with_mock(tmp_path, "golden")

    assert provider.calls == 1
    assert provider.requests[0]["method"] == "POST"
    assert provider.requests[0]["path"] == "/v1/chat/completions"
    assert provider.requests[0]["authorization"] == f"Bearer {SECRET}"
    assert reverse["result_version"] == "reverse-quality-v0.1"
    assert reverse["result"]["fields"]["lifecycle_stage"]["value"] == "运行执行"
    assert reverse["missing_information"] == []
    assert SECRET.encode("utf-8") not in _runtime_bytes(tmp_path)


def test_qs_pt_002_two_trigger_sources_share_one_state_machine(tmp_path):
    client = _p0_client(tmp_path)

    high = _create_candidate(
        client,
        trigger_source="HIGH_PERCEPTION",
        trigger_reason="客户现场高感知问题",
    )
    rnd = _create_candidate(
        client,
        reverse_result=_reverse_result(canonical_itr="ITR-QS-PT-002"),
        trigger_source="RND_VALUE",
        trigger_reason="研发判断具有跨产品复用价值",
    )

    assert high["status"] == "CANDIDATE"
    assert rnd["status"] == "CANDIDATE"
    assert high["trigger_source"] == "HIGH_PERCEPTION"
    assert rnd["trigger_source"] == "RND_VALUE"

    serialized = json.dumps([high, rnd], ensure_ascii=False)
    assert "WAIT_APPROVAL" not in serialized
    assert "WAIT_RND_APPROVAL" not in serialized
    assert "WAIT_QUALITY_APPROVAL" not in serialized


def test_qs_pt_003_pending_missing_information_blocks_confirm(tmp_path):
    client = _p0_client(tmp_path)
    candidate = _create_candidate(
        client,
        reverse_result=_reverse_result(
            missing=[
                {
                    "field_name": "system_scale",
                    "reason": "原问题未给出规模",
                    "question": "现场设备规模是多少？",
                    "evidence_needed": ["现场拓扑"],
                    "status": "PENDING",
                    "answer": "",
                }
            ]
        ),
    )

    reviewed = client.post(
        f"/api/v2/quality-scenarios/{candidate['scenario_id']}/review",
        json={
            "expected_scenario_version": candidate["scenario_version"],
            "patch": {},
            "review_status": "CONFIRMED",
            "reviewer": "QUALITY_OWNER_TEST",
        },
    )
    assert reviewed.status_code == 200
    current = reviewed.json()["scenario"]

    blocked = client.post(
        f"/api/v2/quality-scenarios/{candidate['scenario_id']}/confirm",
        json={
            "expected_scenario_version": current["scenario_version"],
            "quality_confirmed_by": "QUALITY_OWNER_TEST",
            "technical_confirmed_by": "RND_OWNER_TEST",
        },
    )
    assert blocked.status_code == 400
    assert blocked.json()["detail"] == "SCENARIO_MISSING_INFORMATION_PENDING"

    saved = client.get(f"/api/v2/quality-scenarios/{candidate['scenario_id']}").json()
    assert saved["status"] == "CANDIDATE"


@pytest.mark.parametrize(
    "case_name",
    ["evidence_mismatch", "type_error", "ai_unauthorized_state", "empty_content", "invalid_json"],
)
def test_qs_pt_004_ai_invalid_outputs_fail_closed(tmp_path, case_name):
    with pytest.raises(Exception):
        _analyse_with_mock(tmp_path, case_name)


def test_qs_pt_005_api_contract_required_inputs(tmp_path):
    client = _p0_client(tmp_path)

    missing_result = client.post(
        "/api/v2/quality-scenarios/candidates/from-reverse",
        json={"taxonomy": _taxonomy()},
    )
    assert missing_result.status_code == 400
    assert missing_result.json()["detail"] == "REVERSE_QUALITY_RESULT_REQUIRED"

    missing_taxonomy = client.post(
        "/api/v2/quality-scenarios/candidates/from-reverse",
        json={"reverse_quality_result": _reverse_result()},
    )
    assert missing_taxonomy.status_code == 400
    assert missing_taxonomy.json()["detail"] == "SCENARIO_TAXONOMY_REQUIRED"


def test_qs_pt_006_optimistic_concurrency_blocks_stale_write(tmp_path):
    client = _p0_client(tmp_path)
    candidate = _create_candidate(client)

    first = client.post(
        f"/api/v2/quality-scenarios/{candidate['scenario_id']}/review",
        json={
            "expected_scenario_version": candidate["scenario_version"],
            "patch": {"scenario_description": "V2人工修订"},
            "review_status": "PENDING",
            "reviewer": "QUALITY_OWNER_TEST",
        },
    )
    assert first.status_code == 200
    changed = first.json()["scenario"]

    stale = client.post(
        f"/api/v2/quality-scenarios/{candidate['scenario_id']}/review",
        json={
            "expected_scenario_version": candidate["scenario_version"],
            "patch": {"scenario_description": "stale overwrite"},
            "review_status": "PENDING",
            "reviewer": "QUALITY_OWNER_TEST",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "SCENARIO_VERSION_CONFLICT"

    saved = client.get(f"/api/v2/quality-scenarios/{candidate['scenario_id']}").json()
    assert saved["scenario_version"] == changed["scenario_version"]
    assert saved["scenario_description"] == "V2人工修订"


def test_qs_pt_007_runtime_to_publish_calls_provider_once_and_repeat_publish_is_idempotent(tmp_path):
    reverse, taxonomy, provider = _analyse_with_mock(tmp_path, "golden")
    client = _p0_client(tmp_path)

    created = client.post(
        "/api/v2/quality-scenarios/candidates/from-reverse",
        json={
            "reverse_quality_result": reverse["result"],
            "taxonomy": taxonomy,
            "trigger_source": "HIGH_PERCEPTION",
            "trigger_reason": "客户高感知问题",
            "created_by": "PRODUCT_TEST_CENTER",
        },
    )
    assert created.status_code == 200, created.text
    candidate = created.json()["scenario"]

    published = _review_confirm_publish(client, candidate)
    assert published["status"] == "PUBLISHED"
    assert provider.calls == 1

    repeated = client.post(
        f"/api/v2/quality-scenarios/{published['scenario_id']}/publish",
        json={
            "expected_scenario_version": published["scenario_version"] - 1,
            "published_by": "QUALITY_OWNER_TEST",
        },
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["changed"] is False
    assert repeated.json()["scenario"]["scenario_version"] == published["scenario_version"]
    assert provider.calls == 1


def test_qs_pt_008_traceability_and_source_reverse_lookup(tmp_path):
    client = _p0_client(tmp_path)
    published = _review_confirm_publish(client, _create_candidate(client))

    trace = client.get(
        f"/api/v2/quality-scenarios/{published['scenario_id']}/traceability"
    )
    assert trace.status_code == 200, trace.text
    body = trace.json()
    assert body["integrity"]["status"] == "PASS"
    assert body["integrity"]["issues"] == []
    assert body["evidence"]
    assert all(item["source_resolved"] for item in body["evidence"])
    assert all(item["supports_valid"] for item in body["evidence"])
    assert all(
        item["content_status"] in {"CONTROLLED_CONTENT_REF", "INLINE_SOURCE_TEXT"}
        for item in body["evidence"]
    )

    source_ref = published["source_problem_refs"][0]["source_ref"]
    reverse = client.get(
        "/api/v2/quality-scenario-sources/scenarios",
        params={"source_ref": source_ref},
    )
    assert reverse.status_code == 200
    ids = [item["scenario_id"] for item in reverse.json()["items"]]
    assert published["scenario_id"] in ids


def test_qs_pt_009_p02_p03_query_detail_history_and_404(tmp_path):
    client = _p0_client(tmp_path)
    published = _review_confirm_publish(client, _create_candidate(client))

    listing = client.get(
        "/api/v2/quality-scenarios",
        params={
            "status": "PUBLISHED",
            "product_code": "PLC",
            "lifecycle_stage_code": "RUNTIME_EXECUTION",
            "business_activity_code": "POWER_LOSS_RETENTION_RECOVERY",
            "q": "数据完整性",
        },
    )
    assert listing.status_code == 200
    assert published["scenario_id"] in [
        item["scenario_id"] for item in listing.json()["items"]
    ]

    detail = client.get(f"/api/v2/quality-scenarios/{published['scenario_id']}")
    history = client.get(
        f"/api/v2/quality-scenarios/{published['scenario_id']}/history"
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == "PUBLISHED"
    assert detail.json()["confirmation"]["quality_confirmed_by"] == "QUALITY_OWNER_TEST"
    assert detail.json()["confirmation"]["technical_confirmed_by"] == "RND_OWNER_TEST"
    assert history.status_code == 200
    assert history.json()["versions"]
    assert history.json()["reviews"]

    missing = client.get("/api/v2/quality-scenarios/NOT-EXIST")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "QUALITY_SCENARIO_V1_NOT_FOUND"


@pytest.mark.provider_fault
def test_qs_pt_010_429_is_provider_failure_not_product_state(tmp_path):
    with pytest.raises(Exception):
        _analyse_with_mock(tmp_path, "rate_limit")


def test_qs_pt_011_three_product_pages_follow_single_rc1_flow(tmp_path):
    client = _p0_client(tmp_path)
    published = _review_confirm_publish(client, _create_candidate(client))

    p01 = client.get("/p0/quality-scenarios/workbench")
    p02 = client.get("/p0/quality-scenarios")
    p03 = client.get(f"/p0/quality-scenarios/{published['scenario_id']}")

    assert p01.status_code == 200
    assert "场景工作台" in p01.text
    assert p02.status_code == 200
    assert "质量场景库" in p02.text
    assert 'value="PUBLISHED" selected' in p02.text
    assert p03.status_code == 200
    assert "场景详情" in p03.text
    assert "Evidence完整性" in p03.text

    combined = p01.text + p02.text + p03.text
    assert "WAIT_APPROVAL" not in combined
    assert "WAIT_RND_APPROVAL" not in combined
    assert "WAIT_QUALITY_APPROVAL" not in combined


def test_qs_pt_012_reject_is_terminal_for_current_candidate(tmp_path):
    client = _p0_client(tmp_path)
    candidate = _create_candidate(client)

    rejected = client.post(
        f"/api/v2/quality-scenarios/{candidate['scenario_id']}/reject",
        json={
            "expected_scenario_version": candidate["scenario_version"],
            "reviewer": "QUALITY_OWNER_TEST",
            "comment": "不具备标准场景沉淀价值",
        },
    )
    assert rejected.status_code == 200, rejected.text
    rejected_scenario = rejected.json()["scenario"]
    assert rejected_scenario["status"] == "REJECTED"

    publish = client.post(
        f"/api/v2/quality-scenarios/{candidate['scenario_id']}/publish",
        json={
            "expected_scenario_version": rejected_scenario["scenario_version"],
            "published_by": "QUALITY_OWNER_TEST",
        },
    )
    assert publish.status_code == 400
    assert publish.json()["detail"] == "SCENARIO_PUBLISH_REQUIRES_CONFIRMED"

    saved = client.get(f"/api/v2/quality-scenarios/{candidate['scenario_id']}").json()
    assert saved["status"] == "REJECTED"
