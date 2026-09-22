from __future__ import annotations

import os
from pathlib import Path
import sqlite3

import pytest

from builder.repeat_decision import (
    RepeatDecisionDTO,
    RepeatDecisionEngine,
    RuntimeRepeatDecisionAgent,
)
from quality_knowledge.runtime_model_config import (
    resolve_major_runtime_model_config,
)
from runtime import AgentConfigLoader, ConfiguredAgentRuntime, SqliteTaskStore


ROOT = Path(__file__).resolve().parents[1]


def _require_real_provider() -> Path:
    enabled = os.environ.get(
        "MAJOR_REPEAT_REAL_E2E",
        "",
    ).strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        pytest.skip("MAJOR-REPEAT-REAL-E2E opt-in disabled")

    try:
        return resolve_major_runtime_model_config(ROOT)
    except ValueError as exc:
        pytest.fail(str(exc))


def _raw_database_dump(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return "\n".join(connection.iterdump())


def _golden_inputs() -> tuple[dict, dict, dict]:
    context = {
        "query_id": "Q-REPEAT-REAL-GOLDEN",
        "case_id": "CASE-REPEAT-REAL-GOLDEN",
        "query": {
            "standard_query": {
                "problem": {
                    "problem_summary": (
                        "设备运行过程中掉电，配置文件损坏，"
                        "重启后配置无法恢复。"
                    )
                }
            },
            "root_cause": (
                "配置文件直接覆盖写入，掉电窗口产生半写入状态。"
            ),
            "trigger_condition": "配置写入过程中发生掉电。",
        },
        "candidate": {
            "rank": 1,
            "score": 0.98,
        },
        "case": {
            "enriched_case": {
                "problem_summary": (
                    "设备写配置时掉电，配置文件损坏并导致重启失败。"
                ),
                "root_cause": (
                    "配置采用非原子覆盖写，掉电导致文件处于半写入状态。"
                ),
                "trigger_condition": "配置保存窗口发生掉电。",
                "solution": (
                    "采用临时文件写入完成后原子替换，"
                    "并增加掉电恢复验证。"
                ),
            }
        },
        "evidence": {
            "query": [
                "掉电发生在配置保存过程中",
                "重启后无法加载损坏配置",
            ],
            "case": [
                "历史案例确认非原子写是根因",
                "原子替换后问题不再复现",
            ],
        },
        "quality": {"status": "COMPLETE"},
    }
    similarity = {
        "analysis": {
            "overall_score": 96,
            "overall_level": "HIGH",
            "confidence": 0.95,
            "key_similarities": [
                "问题现象一致",
                "触发条件一致",
                "技术根因一致",
            ],
        },
        "analysis_status": "SUCCESS",
    }
    solution = {
        "analysis": {
            "applicability": "DIRECT_REUSE",
            "confidence": 0.92,
        },
        "analysis_status": "SUCCESS",
    }
    return context, similarity, solution


def test_repeat_real_provider_golden_uses_runtime_model_config(
    tmp_path: Path,
) -> None:
    model_config = _require_real_provider()

    loader = AgentConfigLoader(
        root=ROOT,
        model_profiles=model_config,
        schemas={"RepeatDecisionDTO": RepeatDecisionDTO},
        environ=os.environ,
    )
    runtime_db = tmp_path / "repeat-real-runtime.sqlite3"
    store = SqliteTaskStore(runtime_db)
    runtime = ConfiguredAgentRuntime(
        store,
        config_loader=loader,
    )
    engine = RepeatDecisionEngine(
        ROOT,
        runtime=runtime,
    )

    context, similarity, solution = _golden_inputs()
    decision, status, warnings = engine.decide_candidate(
        context,
        similarity,
        solution,
    )

    assert status == "SUCCESS"
    assert decision["decision"] in {
        "REPEAT_CASE",
        "LIKELY_REPEAT",
    }
    assert 0.5 <= float(decision["confidence"]) <= 1.0
    assert decision["decision_reason"]
    assert decision["evidence_chain"]
    assert not any(
        item["code"] == "REPEAT_DECISION_FAILED"
        for item in warnings
    )

    payload = engine._payload(
        context,
        similarity,
        solution,
    )
    request_id = RuntimeRepeatDecisionAgent._request_id(
        context["query_id"],
        context["case_id"],
        payload,
    )
    task = store.get_task_by_request_id(request_id)
    assert task is not None
    provider_calls = store.count_task_provider_calls(task.task_id)
    assert 1 <= provider_calls <= 2

    snapshot = store.get_execution_snapshot(
        task.execution_snapshot_id
    )
    assert "major_issue.repeat_case" in snapshot.model_dump_json()
    assert engine.runtime_agent is not None
    assert engine.runtime_agent.model_name

    secret = loader.get_runtime_api_key(
        engine.runtime_agent.resolved.config_hash,
        api_key_env=(
            engine.runtime_agent.resolved.provider.api_key_env
        ),
    )
    if secret:
        assert secret not in snapshot.model_dump_json()
        assert secret not in _raw_database_dump(runtime_db)
