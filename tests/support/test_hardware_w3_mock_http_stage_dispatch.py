"""Independent W3 test-only real HTTP Stage A/B fixture dispatch, Python 3.11.

Exercises frozen Runtime + Schema validation, not a substitute for TSE browser
G3/G5. Provider and source DB are isolated synthetic local fixtures only.
"""
from __future__ import annotations
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
import os
import runpy
import sys
import threading
import time

import pytest

PRODUCT = Path(__file__).resolve().parents[2] / "product"
assert PRODUCT.is_dir(), "FROZEN_PRODUCT_CHECKOUT_MISSING"
sys.path.insert(0, str(PRODUCT))

from services.hardware_case_markdown_agent import (  # noqa: E402
    R1_AGENT_INPUT_VERSION, R1_REUSE_INPUT_VERSION, run_r1_agent_extraction,
)
from services.hardware_case_r1_runtime import (  # noqa: E402
    HardwareCaseR1PipelineRuntime, HARDWARE_R1_STAGE_A_SCHEMA, HARDWARE_R1_STAGE_B_SCHEMA,
)
from services.hardware_r1_batch_concurrency import execute_case_batch  # noqa: E402
from tools.openai_mock.server import (  # noqa: E402
    Behavior, OpenAIMockHandler, create_server,
)

SAMPLE = runpy.run_path(str(PRODUCT / "tests/test_hardware_r1_pipeline_v13.py"))
synthetic_snapshot = SAMPLE["snapshot"]
fixture_a = SAMPLE["stage_a_payload"]
fixture_b = SAMPLE["stage_b_payload"]


class ContractDispatchHandler(OpenAIMockHandler):
    """Test-only adapter to the existing Mock; no product Provider changes.

    The actual W3 provider sends the stage's input_contract inside the user
    message. The frozen mock normally reads X-Mock-Scenario-Key, which W3 does
    not send. This test handler chooses the configured scenario *after* reading
    the standard OpenAI request, using only the immutable input contract.
    """
    _selected_stage = None

    def _scenario_key(self):
        return self._selected_stage or super()._scenario_key()

    def _chat_completions(self, request):
        try:
            messages = request.get("messages") or []
            payload = json.loads(messages[-1]["content"])
            contract = payload.get("input_contract")
        except (AttributeError, KeyError, IndexError, TypeError, ValueError):
            contract = None
        if contract == R1_AGENT_INPUT_VERSION:
            self._selected_stage = "stage-a"
        elif contract == R1_REUSE_INPUT_VERSION:
            self._selected_stage = "stage-b"
        else:
            return self._json(400, {"error": "UNEXPECTED_W3_INPUT_CONTRACT"})
        return super()._chat_completions(request)


@contextmanager
def running_mock(stage_a, stage_b, *, stage_a_behavior=None, stage_b_behavior=None):
    server = create_server("127.0.0.1", 0)
    server.RequestHandlerClass = ContractDispatchHandler
    server.state.configure("stage-a", stage_a, Behavior.from_dict(stage_a_behavior))
    server.state.configure("stage-b", stage_b, Behavior.from_dict(stage_b_behavior))
    server.state.configure("default", "UNCONFIGURED_FAIL_CLOSED", Behavior.from_dict({}))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def runtime(tmp_path, server):
    conf = tmp_path / "model.local.yaml"
    host,port = server.server_address
    conf.write_text(
        "active_model: w3_mock\nmodels:\n  w3_mock:\n"
        "    provider: openai_compatible\n"
        "    base_url: http://{}:{}/v1\n".format(host,port)
        + "    api_key: W3_DUMMY_NO_REAL_PROVIDER\n"
        "    model: mock-gpt\n    temperature: 0\n    max_tokens: 4096\n",
        encoding="utf-8",
    )
    environment = {
        "HARDWARE_CASE_MODEL_CONFIG": str(conf),
        "HARDWARE_CASE_RUNTIME_DB": str(tmp_path / "audit" / "runtime.db"),
    }
    return HardwareCaseR1PipelineRuntime(root=PRODUCT, environ=environment)


def test_w3_real_http_stage_a_then_b_schema_and_evidence(tmp_path):
    import jsonschema
    a,b=fixture_a(),fixture_b()
    jsonschema.validate(a,HARDWARE_R1_STAGE_A_SCHEMA)
    jsonschema.validate(b,HARDWARE_R1_STAGE_B_SCHEMA)
    with running_mock(a,b) as server:
        pipe=runtime(tmp_path,server)
        result=run_r1_agent_extraction(synthetic_snapshot(),pipe)
        stats=server.state.counters()
        request_records=server.state.requests(None)
    assert result.get("pipeline_status") == "GOLDEN_PREVIEW_READY", result
    assert (result.get("evidence_validation") or {}).get("status") == "PASS", result
    assert stats.get("stage-a") == 1,stats
    assert stats.get("stage-b") == 1,stats
    assert stats.get("default",0) == 0,stats
    stages=[p.get("scenario_key") for p in request_records]
    assert stages == ["stage-a","stage-b"],stages
    assert int(result.get("provider_call_count",0)) == 2,result


def test_w3_stage_b_http_error_preserves_a_and_fail_closed(tmp_path):
    with running_mock(fixture_a(),fixture_b(),stage_b_behavior={"status":503}) as server:
        pipe=runtime(tmp_path,server)
        result=run_r1_agent_extraction(synthetic_snapshot(),pipe)
        stats=server.state.counters()
    assert result.get("failed_stage") == "STAGE_B",result
    assert stats.get("stage-a",0) == 1,stats
    assert 1 <= stats.get("stage-b",0) <= 2,stats
    assert "knowledge_object" not in result or not result["knowledge_object"],result
    assert stats.get("default",0)==0,stats


def test_w3_invalid_a_schema_never_enters_b(tmp_path):
    with running_mock({"bad":"invalid"},fixture_b()) as server:
        pipe=runtime(tmp_path,server)
        result=run_r1_agent_extraction(synthetic_snapshot(),pipe)
        stats=server.state.counters()
    assert result.get("failed_stage") == "STAGE_A",result
    assert 1 <= stats.get("stage-a",0) <= 2,stats
    assert stats.get("stage-b",0)==0,stats


def test_w3_bounded_parallel_distinct_cases_via_frozen_runtime(tmp_path):
    snapshots=[]
    for case in ("A0152","A0153"):
        snap=deepcopy(synthetic_snapshot())
        snap["identity"]["business_case_id"]=case
        snap["source"]["source_id"]="independent-source-"+case
        snapshots.append(snap)
    with running_mock(fixture_a(),fixture_b(),stage_a_behavior={"delay_ms":90},stage_b_behavior={"delay_ms":90}) as server:
        pipe=runtime(tmp_path,server)
        outputs={}
        lock=threading.Lock()
        def worker(s):
            result=run_r1_agent_extraction(s,pipe)
            with lock:
                outputs[s["identity"]["business_case_id"]]=result
        execute_case_batch(snapshots,worker=worker,execution_mode="PARALLEL",concurrency=2)
        stats=server.state.counters()
    assert len(outputs)==2,outputs.keys()
    assert all(x.get("pipeline_status")=="GOLDEN_PREVIEW_READY" for x in outputs.values()),outputs
    assert stats.get("stage-a",0)==2 and stats.get("stage-b",0)==2,stats
    assert stats.get("default",0)==0,stats
