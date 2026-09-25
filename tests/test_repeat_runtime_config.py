from __future__ import annotations

from contextlib import contextmanager
import inspect
import json
from pathlib import Path
import sqlite3
import threading
from typing import Iterator
from urllib.request import Request, urlopen

import yaml

import builder.repeat_decision as repeat_decision_module
from builder.repeat_decision import (
    RepeatDecisionEngine,
    RuntimeRepeatDecisionAgent,
)
from tools.openai_mock.server import create_server


ROOT = Path(__file__).resolve().parents[1]
SECRET = "REPEAT_RUNTIME_SECRET_MUST_NOT_PERSIST"


@contextmanager
def running_server() -> Iterator[tuple[str, int]]:
    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def configure(host: str, port: int, payload, behavior=None) -> None:
    raw = json.dumps(
        {
            "scenario_key": "default",
            "payload": payload,
            "behavior": behavior or {},
        },
        ensure_ascii=False,
    ).encode()
    request = Request(
        f"http://{host}:{port}/__mock__/scenario",
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=2) as response:
        assert response.status == 200


def counters(host: str, port: int) -> dict[str, int]:
    with urlopen(
        f"http://{host}:{port}/__mock__/counters",
        timeout=2,
    ) as response:
        return json.loads(response.read().decode())["data"]


def _copy(root: Path, rel: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        (ROOT / rel).read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def prepare_runtime_root(root: Path) -> None:
    for rel in (
        "config/repeat_decision.yaml",
        "prompts/repeat_decision.md",
        "schema/repeat_analysis.schema.json",
        "config/runtime/agents/major_issue.repeat_case.yaml",
    ):
        _copy(root, rel)
    (root / "output/logs").mkdir(parents=True, exist_ok=True)

    model_cfg = {
        "repeat_decision_ai": {
            "enabled": True,
            "prompt_version": "M8.4-P1",
        }
    }
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config/model.yaml").write_text(
        yaml.safe_dump(
            model_cfg,
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def model_config(root: Path, base_url: str) -> Path:
    path = root / "config/model.local.yaml"
    path.write_text(
        f"""
active_model: qwen_prod
models:
  qwen_prod:
    provider: openai_compatible
    base_url: {base_url}
    api_key: {SECRET}
    model: qwen3.8-max
    temperature: 0
    max_tokens: 8192
""".strip(),
        encoding="utf-8",
    )
    return path


def decision_payload() -> dict:
    return {
        "decision": "LIKELY_REPEAT",
        "confidence": 0.88,
        "decision_reason": "问题现象与技术根因高度接近，仍需确认触发条件。",
        "evidence_chain": [
            {
                "dimension": "root_cause",
                "strength": "STRONG",
                "query_evidence": ["配置写入非原子"],
                "case_evidence": ["历史案例同样存在非原子写入"],
                "reason": "技术根因一致",
            }
        ],
        "key_differences": ["触发时机仍需确认"],
        "validation_required": ["确认掉电窗口"],
        "risks": [],
        "recommended_actions": ["复用历史掉电测试用例"],
    }


def context_payload() -> tuple[dict, dict, dict]:
    context = {
        "query_id": "Q-RUNTIME-1",
        "case_id": "CASE-HISTORY-1",
        "query": {
            "standard_query": {
                "problem": {
                    "problem_summary": "掉电后配置损坏"
                }
            }
        },
        "candidate": {"rank": 1, "score": 0.92},
        "case": {
            "enriched_case": {
                "root_cause": "配置写入非原子"
            }
        },
        "evidence": {"fragments": ["E1"]},
        "quality": {"status": "COMPLETE"},
    }
    similarity = {
        "analysis": {
            "overall_score": 92,
            "overall_level": "HIGH",
        },
        "analysis_status": "SUCCESS",
    }
    solution = {
        "analysis": {
            "applicability": "PARTIAL_REUSE",
            "confidence": 0.8,
        },
        "analysis_status": "SUCCESS",
    }
    return context, similarity, solution


def raw_database_dump(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return "\n".join(connection.iterdump())


def test_repeat_decision_uses_unified_runtime_and_external_model_config(
    tmp_path: Path,
) -> None:
    prepare_runtime_root(tmp_path)
    with running_server() as (host, port):
        base_url = f"http://{host}:{port}/v1"
        config = model_config(tmp_path, base_url)
        configure(host, port, decision_payload())

        runtime_db = tmp_path / "repeat-runtime.db"
        engine = RepeatDecisionEngine(
            tmp_path,
            model_config_path=config,
            runtime_db_path=runtime_db,
        )
        context, similarity, solution = context_payload()

        decision, status, warnings = engine.decide_candidate(
            context,
            similarity,
            solution,
        )

        assert status == "SUCCESS"
        assert decision["decision"] == "LIKELY_REPEAT"
        assert decision["confidence"] == 0.88
        assert warnings == []
        assert counters(host, port)["default"] == 1
        assert engine.runtime_agent is not None
        assert engine.runtime_agent.resolved.definition.agent_id == (
            "major_issue.repeat_case"
        )
        assert engine.runtime_agent.model_name == "qwen3.8-max"

        payload = engine._payload(context, similarity, solution)
        request_id = RuntimeRepeatDecisionAgent._request_id(
            "Q-RUNTIME-1",
            "CASE-HISTORY-1",
            payload,
        )
        store = engine.runtime_agent.runtime.store
        task = store.get_task_by_request_id(request_id)
        assert task is not None
        assert store.count_task_provider_calls(task.task_id) == 1
        assert SECRET not in raw_database_dump(runtime_db)


def test_repeat_decision_retry_is_runtime_owned(tmp_path: Path) -> None:
    prepare_runtime_root(tmp_path)
    with running_server() as (host, port):
        base_url = f"http://{host}:{port}/v1"
        config = model_config(tmp_path, base_url)
        configure(
            host,
            port,
            decision_payload(),
            {
                "fail_first_n": 1,
                "fail_status": 429,
                "retry_after": "0",
            },
        )

        engine = RepeatDecisionEngine(
            tmp_path,
            model_config_path=config,
            runtime_db_path=tmp_path / "repeat-retry.db",
        )
        context, similarity, solution = context_payload()

        decision, status, _ = engine.decide_candidate(
            context,
            similarity,
            solution,
        )

        assert status == "SUCCESS"
        assert decision["decision"] == "LIKELY_REPEAT"
        assert counters(host, port)["default"] == 2

        payload = engine._payload(context, similarity, solution)
        request_id = RuntimeRepeatDecisionAgent._request_id(
            "Q-RUNTIME-1",
            "CASE-HISTORY-1",
            payload,
        )
        store = engine.runtime_agent.runtime.store
        task = store.get_task_by_request_id(request_id)
        assert store.count_task_provider_calls(task.task_id) == 2


def test_repeat_production_path_has_no_business_provider_or_retry_loop() -> None:
    source = inspect.getsource(RepeatDecisionEngine)
    assert "OpenAICompatibleClient(" not in source
    assert "for attempt in (1, 2)" not in source
    assert "time.sleep(" not in source


def test_repeat_disabled_still_skips_without_runtime(tmp_path: Path) -> None:
    prepare_runtime_root(tmp_path)
    cfg = yaml.safe_load(
        (tmp_path / "config/model.yaml").read_text(encoding="utf-8")
    )
    cfg["repeat_decision_ai"]["enabled"] = False
    (tmp_path / "config/model.yaml").write_text(
        yaml.safe_dump(
            cfg,
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    engine = RepeatDecisionEngine(tmp_path)
    context, similarity, solution = context_payload()
    decision, status, warnings = engine.decide_candidate(
        context,
        similarity,
        solution,
    )

    assert status == "SKIPPED"
    assert decision["decision"] == "INSUFFICIENT_EVIDENCE"
    assert engine.runtime_agent is None
    assert any(
        item["code"] == "DECISION_AI_SKIPPED"
        for item in warnings
    )


def test_repeat_runtime_invalid_structured_output_fails_without_business_json_repair(
    tmp_path: Path,
    monkeypatch,
) -> None:
    prepare_runtime_root(tmp_path)
    with running_server() as (host, port):
        config = model_config(tmp_path, f"http://{host}:{port}/v1")
        invalid_payload = decision_payload()
        invalid_payload["decision"] = "NOT_A_REPEAT_DECISION"
        configure(host, port, invalid_payload)

        repair_calls = 0

        def forbidden_business_repair(_content):
            nonlocal repair_calls
            repair_calls += 1
            raise AssertionError("business JSON repair must not run on Runtime path")

        monkeypatch.setattr(
            repeat_decision_module,
            "_extract_json",
            forbidden_business_repair,
        )

        engine = RepeatDecisionEngine(
            tmp_path,
            model_config_path=config,
            runtime_db_path=tmp_path / "repeat-invalid-output.db",
        )
        context, similarity, solution = context_payload()

        decision, status, warnings = engine.decide_candidate(
            context,
            similarity,
            solution,
        )

        assert status == "FAILED"
        assert decision["decision"] == "INSUFFICIENT_EVIDENCE"
        assert repair_calls == 0
        assert not any(item["code"] == "AI_JSON_REPAIRED" for item in warnings)
        provider_calls = counters(host, port)["default"]
        assert 1 <= provider_calls <= 2
