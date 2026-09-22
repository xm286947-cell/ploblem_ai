from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

pytest.importorskip("runtime")
try:
    from tools.openai_mock.server import Behavior, create_server
except ModuleNotFoundError:
    pytest.skip("Unified Runtime OpenAI Mock dependency is not available", allow_module_level=True)

from quality_knowledge.reverse_quality_runtime import (
    AGENT_ID,
    ReverseQualityRuntimeExecutor,
)


ROOT = Path(__file__).resolve().parents[1]
AGENT_CONFIG = ROOT / "config/runtime/agents/reverse_quality.single_issue.analyze.yaml"
SECRET = "REVERSE_QUALITY_RUNTIME_TEST_SECRET"


@contextmanager
def running_server():
    server=create_server("127.0.0.1",0)
    thread=threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval":0.01},
        daemon=True,
    )
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def result_payload():
    return {
        "fields":{
            "customer_experience":{
                "value":"掉电后关键计数丢失",
                "evidence_ids":["cs.description"],
                "confidence":0.9,
            },
            "preconditions":{
                "value":"PLC 正常运行时",
                "evidence_ids":["cs.description"],
                "confidence":0.8,
            },
        },
        "lifecycle_code":"RUNTIME_EXECUTION",
        "activity_code":"POWER_LOSS_RETENTION_RECOVERY",
        "match_reason":"证据指向掉电恢复活动",
        "missing_condition":"",
        "questions":[],
    }


def model_config(tmp_path,base_url):
    path=tmp_path/"model.local.yaml"
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


def raw_runtime_bytes(tmp_path):
    return b"".join(
        path.read_bytes()
        for path in sorted(tmp_path.glob("runtime.db*"))
        if path.is_file()
    )


def test_reverse_quality_runtime_mock_e2e_model_ref_schema_and_secret_safety(tmp_path):
    with running_server() as server:
        server.state.configure(
            "default",
            json.dumps(result_payload(),ensure_ascii=False),
            Behavior(require_auth=True),
        )
        host,port=server.server_address
        executor=ReverseQualityRuntimeExecutor(
            ROOT,
            tmp_path/"runtime.db",
            model_config_path=model_config(tmp_path,f"http://{host}:{port}/v1"),
            agent_config_path=AGENT_CONFIG,
            environ={},
        )

        assert executor.resolved.definition.agent_id==AGENT_ID
        assert executor.resolved.provider.profile_ref=="qwen_prod"
        assert executor.resolved.provider.model=="mock-gpt"
        assert SECRET not in executor.resolved.model_dump_json()

        result=executor.execute(
            {
                "facts":{"canonical_itr":"ITR-MOCK-001"},
                "taxonomy":{"lifecycles":[],"activities":[]},
                "quality_characteristics":[],
                "field_names":["customer_experience","preconditions"],
            },
            request_id="reverse-quality-runtime-mock-001",
        )

        assert result.data==result_payload()
        assert result.model=="mock-gpt"
        assert result.provider_calls==1
        assert server.state.counters()["default"]==1
        request=server.state.requests("default")[0]
        assert request["method"]=="POST"
        assert request["path"]=="/v1/chat/completions"
        assert request["model"]=="mock-gpt"
        assert request["authorization"]=={"present":True,"scheme":"Bearer"}
        assert request["headers"]["Authorization"]=="[REDACTED]"
        snapshot=executor.store.get_execution_snapshot(result.execution_snapshot_id)
        assert SECRET not in snapshot.model_dump_json()
        assert SECRET not in json.dumps(result.data,ensure_ascii=False)
        assert SECRET.encode("utf-8") not in raw_runtime_bytes(tmp_path)


def test_reverse_quality_runtime_retry_is_runtime_owned_and_counted(tmp_path):
    with running_server() as server:
        server.state.configure(
            "default",
            json.dumps(result_payload(),ensure_ascii=False),
            Behavior(
                require_auth=True,
                fail_first_n=1,
                fail_status=429,
                retry_after="0",
            ),
        )
        host,port=server.server_address
        executor=ReverseQualityRuntimeExecutor(
            ROOT,
            tmp_path/"runtime.db",
            model_config_path=model_config(tmp_path,f"http://{host}:{port}/v1"),
            agent_config_path=AGENT_CONFIG,
            environ={},
        )

        result=executor.execute(
            {"facts":{"canonical_itr":"ITR-MOCK-RETRY"}},
            request_id="reverse-quality-runtime-mock-retry",
        )

        assert result.data==result_payload()
        assert result.provider_calls==2
        assert server.state.counters()["default"]==2
        assert SECRET.encode("utf-8") not in raw_runtime_bytes(tmp_path)


def test_reverse_quality_service_uses_runtime_and_preserves_result_contract(tmp_path):
    from quality_knowledge.web.app import create_app

    with running_server() as server:
        payload=result_payload()
        payload["fields"].update({
            "expected_quality_state":{
                "value":"重新上电后计数应正确恢复",
                "evidence_ids":["cs.description"],
                "confidence":0.8,
            },
            "root_cause":{
                "value":"保持变量写入未完成",
                "evidence_ids":["cs.root_cause"],
                "confidence":1.0,
            },
            "recovery_method":{
                "value":"重新上电恢复运行",
                "evidence_ids":["structured.recovery_measure"],
                "confidence":0.95,
            },
            "related_objects":{
                "value":"PLC AM600",
                "evidence_ids":["structured.product_model"],
                "confidence":0.9,
            },
            "quality_requirement_candidate":{
                "value":"异常掉电后关键运行数据能够正确恢复",
                "evidence_ids":["cs.description","cs.root_cause"],
                "confidence":0.75,
            },
            "lifecycle_stage":{
                "value":"运行执行",
                "evidence_ids":["cs.description","cs.phase"],
                "confidence":0.85,
            },
            "business_activity_scene":{
                "value":"掉电数据保持与上电恢复",
                "evidence_ids":["cs.description"],
                "confidence":0.9,
            },
        })
        payload["questions"]=[{
            "field_name":"scale_or_load",
            "reason":"原始问题未给出系统规模",
            "question":"现场参与设备规模是多少？",
            "evidence_needed":["现场拓扑或设备数量"],
        }]
        server.state.configure(
            "default",
            json.dumps(payload,ensure_ascii=False),
            Behavior(require_auth=True),
        )
        host,port=server.server_address

        app=create_app(tmp_path/"reverse.db")
        service=app.state.reverse_quality_service
        service.ai_client=None
        service._runtime_executor=ReverseQualityRuntimeExecutor(
            ROOT,
            tmp_path/"runtime.db",
            model_config_path=model_config(tmp_path,f"http://{host}:{port}/v1"),
            agent_config_path=AGENT_CONFIG,
            environ={},
        )
        repo=app.state.material_repository
        material_id,_=repo.add_material(
            repo.group("ITR-CS"),
            "ITR20260918001CS",
            {
                "问题信息_问题描述":"PLC 正常运行时异常掉电，重新上电后关键计数丢失",
                "问题信息_问题原因定位":"保持变量写入未完成",
                "问题信息_问题发生阶段":"终端正常使用",
                "问题信息_产品型号":"PLC AM600",
                "问题信息_问题领域":"软件",
                "问题处理结果_问题解决方案":"重新上电恢复运行",
            },
            "synthetic.xlsx","Sheet1",2,
        )

        saved=service.analyse(material_id,"PLC")

        assert server.state.counters()["default"]==1
        assert saved["result_version"]=="reverse-quality-v0.1"
        assert saved["model"]=="mock-gpt"
        assert saved["review"]["lifecycle_stage"]["value"]=="运行执行"
        assert saved["review"]["business_activity_scene"]["value"]=="掉电数据保持与上电恢复"
        assert saved["review"]["preconditions"]["value"]=="PLC 正常运行时"
        assert saved["review"]["recovery_method"]["value"]=="重新上电恢复运行"
        assert saved["scene_match_status"]=="NEED_REVIEW"
        assert saved["missing_information"][0]["field_name"]=="scale_or_load"
        assert saved["missing_information"][0]["status"]=="PENDING"
        assert SECRET.encode("utf-8") not in raw_runtime_bytes(tmp_path)

