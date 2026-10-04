from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("runtime")

from quality_knowledge.reverse_quality_runtime import (
    AGENT_ID,
    ReverseQualityRuntimeExecutor,
)


ROOT = Path(__file__).resolve().parents[1]
AGENT_CONFIG = ROOT / "config/runtime/agents/reverse_quality.single_issue.analyze.yaml"
SECRET = "REVERSE_QUALITY_RUNTIME_TEST_SECRET"


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


class _State:
    def __init__(self, payload, *, fail_first_n=0):
        self.payload=payload
        self.fail_first_n=fail_first_n
        self.calls=0
        self.requests=[]


@contextmanager
def running_server(payload=None, *, fail_first_n=0):
    state=_State(payload if payload is not None else result_payload(),fail_first_n=fail_first_n)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length=int(self.headers.get("Content-Length","0"))
            body=json.loads(self.rfile.read(length).decode("utf-8"))
            state.calls+=1
            state.requests.append({
                "method":"POST",
                "path":self.path,
                "model":body.get("model"),
                "authorization":self.headers.get("Authorization"),
            })
            if state.calls <= state.fail_first_n:
                raw=json.dumps({"error":{"message":"retry"}}).encode("utf-8")
                self.send_response(429)
                self.send_header("Content-Type","application/json")
                self.send_header("Retry-After","0")
                self.send_header("Content-Length",str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            envelope={
                "choices":[{
                    "message":{"content":json.dumps(state.payload,ensure_ascii=False)},
                    "finish_reason":"stop",
                }]
            }
            raw=json.dumps(envelope,ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format, *args):
            return

    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        yield server,state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


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


def executor_for(tmp_path,base_url):
    return ReverseQualityRuntimeExecutor(
        ROOT,
        tmp_path/"runtime.db",
        model_config_path=model_config(tmp_path,base_url),
        agent_config_path=AGENT_CONFIG,
        environ={},
    )


def test_reverse_quality_runtime_mock_e2e_model_ref_schema_and_secret_safety(tmp_path):
    with running_server() as (server,state):
        host,port=server.server_address
        executor=executor_for(tmp_path,f"http://{host}:{port}/v1")

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
        assert state.calls==1
        request=state.requests[0]
        assert request["method"]=="POST"
        assert request["path"]=="/v1/chat/completions"
        assert request["model"]=="mock-gpt"
        assert request["authorization"]==f"Bearer {SECRET}"
        snapshot=executor.store.get_execution_snapshot(result.execution_snapshot_id)
        assert SECRET not in snapshot.model_dump_json()
        assert SECRET not in json.dumps(result.data,ensure_ascii=False)
        assert SECRET.encode("utf-8") not in raw_runtime_bytes(tmp_path)


def test_reverse_quality_runtime_retry_is_runtime_owned_and_counted(tmp_path):
    with running_server(fail_first_n=1) as (server,state):
        host,port=server.server_address
        executor=executor_for(tmp_path,f"http://{host}:{port}/v1")

        result=executor.execute(
            {"facts":{"canonical_itr":"ITR-MOCK-RETRY"}},
            request_id="reverse-quality-runtime-mock-retry",
        )

        assert result.data==result_payload()
        assert result.provider_calls==2
        assert state.calls==2
        assert SECRET.encode("utf-8") not in raw_runtime_bytes(tmp_path)


def test_reverse_quality_service_uses_runtime_and_preserves_result_contract(tmp_path):
    from types import SimpleNamespace

    from quality_knowledge.materials import MaterialRepository
    from quality_knowledge.reverse_quality import ReverseQualityService
    from quality_knowledge.reverse_quality_bundle_bridge import ReverseQualityBundleBridge
    from quality_knowledge.scenario_source_bundle_v1 import ScenarioSourceBundleV1SnapshotStore, build_scenario_source_bundle_v1

    payload={"fields":{},"lifecycle_code":"","activity_code":"","match_reason":"","missing_condition":"","questions":[]}
    with running_server(payload) as (server,state):
        host,port=server.server_address
        db=tmp_path/"reverse.db"
        repo=MaterialRepository(db)
        assessment_id=repo.add_material(repo.group("SW-OPS"),"ITR20260918001",{
            "问题信息_问题描述":"冻结 Bundle 描述", "问题信息_产品型号":"PLC AM600",
        },"synthetic.xlsx","Sheet1",2)[0]
        resolution_id=repo.add_material(repo.group("ITR-CS"),"ITR20260918001CS",{
            "问题信息_问题原因定位":"冻结 Bundle 根因",
        },"synthetic.xlsx","Sheet1",2)[0]
        with repo.connect() as connection:
            assessment=dict(connection.execute(
                "SELECT m.*,g.group_code FROM source_material m JOIN data_group g USING(group_id) WHERE material_id=?",
                (assessment_id,),
            ).fetchone())
            resolution=dict(connection.execute(
                "SELECT m.*,g.group_code FROM source_material m JOIN data_group g USING(group_id) WHERE material_id=?",
                (resolution_id,),
            ).fetchone())
        snapshot={
            "selected_issue":{
                "knowledge_id":"ISSUE-RQ-BUNDLE", "business_issue_id":"ITR20260918001",
                "software_assessment_record_id":assessment_id,"software_assessment_revision":assessment["source_hash"],
                "product_code":"PLC","product_model":"PLC AM600","ipmt":"","spdt":"","industry":"","customer":"",
            },
            "source_refs":[
                {"source_type":"SOFTWARE_ASSESSMENT","source_id":assessment_id,
                 "source_revision":assessment["source_hash"],"version_no":assessment["version_no"],
                 "business_key":assessment["business_key"],"group_code":assessment["group_code"],
                 "relation_type":"PRIMARY_SOURCE","binding_status":"BOUND","evidence_kind":"SOURCE_MATERIAL"},
                {"source_type":"RESOLUTION","source_id":resolution_id,
                 "source_revision":resolution["source_hash"],"version_no":resolution["version_no"],
                 "business_key":resolution["business_key"],"group_code":resolution["group_code"],
                 "relation_type":"SUPPORTING_EVIDENCE","binding_status":"BOUND","evidence_kind":"SOURCE_MATERIAL"},
            ],
            "normalized_facts":{},"effective_analysis":{},"snapshot_metadata":{
                "snapshot_id":"SNAP-RQ-E2E","built_at":"2026-10-05T00:00:00Z","builder_version":"scenario-source-snapshot/v1",
                "selected_issue_count":1,"source_count":1,"source_hashes":[assessment["source_hash"]],
            },
        }
        bundle=build_scenario_source_bundle_v1(snapshot,evidence_repository=repo)
        description_evidence=next(item["evidence_id"] for item in bundle["field_evidence"] if item["target_field"]=="problem_description")
        root_evidence=next(item["evidence_id"] for item in bundle["field_evidence"] if item["target_field"]=="root_cause")
        state.payload={
            "fields":{
                "customer_experience":{"value":"客户看到运行问题","evidence_ids":[description_evidence],"confidence":0.9},
                "root_cause":{"value":"冻结 Bundle 根因","evidence_ids":[root_evidence],"confidence":1.0},
            },
            "lifecycle_code":"","activity_code":"","match_reason":"","missing_condition":"","questions":[],
        }
        bundle_store=ScenarioSourceBundleV1SnapshotStore(db)
        bundle_store.save(bundle)

        class ScenarioStore:
            db_path=str(db)

            @staticmethod
            def connect():
                import sqlite3
                return sqlite3.connect(db)

            @staticmethod
            def quality_models():
                return {"product_characteristics":[]}

            @staticmethod
            def scenarios(**_kwargs):
                return []

        service=ReverseQualityService(
            repo,ScenarioStore(),None,ROOT,repository=None,
            runtime_executor=executor_for(tmp_path,f"http://{host}:{port}/v1"),
        )
        # The source row can change after Bundle freeze; the bridge must not re-read it.
        with repo.connect() as connection:
            connection.execute("UPDATE source_material SET raw_json=? WHERE material_id=?",(
                json.dumps({"问题信息_问题描述":"Mutated after freeze"},ensure_ascii=False),assessment_id,
            ))
            connection.execute("UPDATE source_material SET raw_json=? WHERE material_id=?",(
                json.dumps({"问题信息_问题原因定位":"Mutated root"},ensure_ascii=False),resolution_id,
            ))
        saved=ReverseQualityBundleBridge(service,bundle_store).analyse(bundle,taxonomy={"version_id":"TAX-1","lifecycles":[],"activities":[]})

        assert state.calls==1
        assert saved["result_version"]=="reverse-quality-v0.1"
        assert saved["model"]=="mock-gpt"
        assert saved["bundle_provenance"]["bundle_id"]==bundle["bundle_id"]
        assert saved["bundle_provenance"]["bundle_revision"]==bundle["bundle_revision"]
        assert saved["bundle_provenance"]["snapshot_id"]=="SNAP-RQ-E2E"
        assert saved["result"]["identity"]["bundle_id"]==bundle["bundle_id"]
        assert saved["review"]["root_cause"]["value"]=="冻结 Bundle 根因"
        assert saved["review"]["root_cause"]["evidence_ids"]==[root_evidence]
        assert saved["input"]["evidence"][next(iter(bundle["field_evidence"]))["evidence_id"]]["value"]=="冻结 Bundle 描述"
        assert saved["scene_match_status"]=="NEED_REVIEW"
        assert any(item["code"]=="MISSED_TEST_EFFECTIVE_ANALYSIS_MISSING" for item in saved["bundle_provenance"]["missing_information"])
        assert SECRET.encode("utf-8") not in raw_runtime_bytes(tmp_path)


def test_rcfg03_business_code_has_no_direct_provider_or_retry_path():
    service_source=(ROOT/"quality_knowledge"/"reverse_quality.py").read_text(encoding="utf-8")
    executor_source=(ROOT/"quality_knowledge"/"reverse_quality_runtime.py").read_text(encoding="utf-8")
    forbidden=(
        "OpenAICompatibleClient",
        "load_quality_issue_ai_config",
        "urlopen(",
        "requests.post(",
        "httpx.",
    )
    for marker in forbidden:
        assert marker not in service_source
        assert marker not in executor_source

    assert "AgentConfigLoader" in executor_source
    assert "ConfiguredAgentRuntime" in executor_source
    assert "model_ref" not in service_source
