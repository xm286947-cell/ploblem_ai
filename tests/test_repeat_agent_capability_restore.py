from __future__ import annotations

import json
from pathlib import Path
import threading

import yaml

from quality_knowledge.repeat_risk.agent_analysis import (
    RepeatAgentAnalysisService,
    RepeatSimilarityDTO,
    RepeatSolutionDTO,
)
from quality_knowledge.repeat_risk.repository import RepeatQueryTraceRepository
from quality_knowledge.repeat_risk.result import RepeatResultService
from quality_knowledge.repeat_risk.search import (
    RepeatHistoricalCaseSearchService,
    SEARCH_SUCCESS,
)
from tools.openai_mock.server import Behavior, create_server


ROOT = Path(__file__).parents[1]


def _dimension(score: int = 80):
    return {
        "score": score,
        "assessment": "SIMILAR",
        "query_evidence": ["current evidence"],
        "case_evidence": ["historical evidence"],
        "reason": "evidence-aligned comparison",
    }


def _similarity_analysis():
    return {
        "dimensions": {
            "problem_object": _dimension(),
            "phenomenon": _dimension(),
            "trigger_condition": _dimension(),
            "impact": _dimension(),
            "failure_mechanism": _dimension(),
            "trc": _dimension(),
            "mrc": _dimension(),
            "root_cause": _dimension(),
            "classification": _dimension(),
            "organization_context": _dimension(),
        },
        "overall_score": 82,
        "overall_level": "HIGH",
        "key_similarities": ["现象和触发条件相近"],
        "key_differences": ["版本不同"],
        "evidence_gaps": [],
        "analysis_summary": "历史案例与当前问题存在较强语义关联。",
        "confidence": 0.86,
    }


def _similarity_wrapper():
    return {
        "metadata": {"query_id": "RQ-1", "case_id": "HCASE-1"},
        "retrieval": {"rank": 1, "retrieval_score": 0.87},
        "analysis": _similarity_analysis(),
        "analysis_status": "SUCCESS",
        "warnings": [],
    }


def _solution_wrapper():
    return {
        "metadata": {"query_id": "RQ-1", "case_id": "HCASE-1"},
        "similarity_reference": {
            "overall_score": 82,
            "overall_level": "HIGH",
            "analysis_status": "SUCCESS",
        },
        "analysis": {
            "historical_solution_summary": "历史案例通过原子保存修复。",
            "corrective_actions": ["增加原子保存"],
            "preventive_actions": ["补充掉电测试"],
            "verification_evidence": ["100 次掉电验证通过"],
            "closure_status": "CLOSED",
            "effectiveness": "EFFECTIVE",
            "applicability": "PARTIAL_REUSE",
            "reusable_actions": ["复用原子保存机制"],
            "adaptation_required": ["结合当前版本确认路径"],
            "reuse_risks": ["根因未确认前不能直接复制措施"],
            "evidence_gaps": [],
            "analysis_summary": "历史措施可参考，但需先确认当前根因。",
            "confidence": 0.88,
        },
        "analysis_status": "SUCCESS",
        "warnings": [],
    }


def _candidate():
    return {
        "case_id": "HCASE-1",
        "title": "历史掉电恢复案例",
        "summary": "历史案例",
        "retrieval_score": 0.87,
        "rank": 1,
        "retrieval_reason": ["问题均发生于掉电恢复场景"],
        "matched_fields": ["problem", "cause"],
        "historical_phenomenon": "掉电恢复后启动失败",
        "problem_description": "掉电恢复后启动失败",
        "root_causes": ["保存路径存在未完成写入"],
        "measures": ["增加原子保存"],
        "verification": "100 次掉电恢复验证通过",
        "evidence_refs": [{"source_id": "ITR-H-1", "page": 7}],
        "evidence": [{
            "evidence_id": "E-1",
            "source_type": "REPORT",
            "source_id": "ITR-H-1",
            "file_name": "history.pdf",
            "page": 7,
            "section": "Root Cause",
            "raw_text": "掉电窗口存在未完成写入。",
        }],
        "source_ref": "ITR-H-1",
        "source_refs": ["ITR-H-1"],
        "detail_status": "SUCCESS",
        "detail_error": None,
        "case_status": "PUBLISHED",
        "semantic_mode": "LEGACY_GENERIC_ONLY",
        "semantic_contract_version": None,
        "typed_causes": [],
        "typed_actions": [],
        "semantic_coverage": {},
        "semantic_evidence_status": "LEGACY_GENERIC_ONLY",
    }


def _search_result():
    return {
        "query_id": "RQ-1",
        "subject_ref": "ITR-1",
        "query_input": {
            "text": "当前控制器掉电后启动失败",
            "cause_description": "保存路径异常",
            "solution": "",
            "organization": {
                "ipmt": "IPMT-A",
                "spdt": "SPDT-A",
                "responsible_department_level2": "研发二部",
            },
            "filters": {"product": "PLC", "domain": "软件"},
            "classification": {
                "cause_level1": "软件设计",
                "cause_level2": "资源管理",
            },
        },
        "historical_case_contract": "historical-case/v1",
        "status": SEARCH_SUCCESS,
        "candidates": [_candidate()],
        "error_code": None,
    }


def test_m82_m83_runtime_dtos_keep_mature_strict_shapes():
    RepeatSimilarityDTO.model_validate(_similarity_analysis())
    RepeatSolutionDTO.model_validate(_solution_wrapper()["analysis"])


def test_runtime_agent_configs_reuse_mature_prompts():
    similarity = yaml.safe_load(
        (ROOT / "config/runtime/agents/major_issue.repeat_similarity.yaml").read_text(
            encoding="utf-8"
        )
    )
    solution = yaml.safe_load(
        (ROOT / "config/runtime/agents/major_issue.repeat_solution.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert similarity["prompt"]["ref"] == "prompts/similarity_analyzer.md"
    assert solution["prompt"]["ref"] == "prompts/solution_analyzer.md"
    assert similarity["metadata"]["what_how_boundary"] == "RUNTIME_EXECUTION_ONLY"
    assert solution["metadata"]["what_how_boundary"] == "RUNTIME_EXECUTION_ONLY"


def test_repeat_agent_configs_follow_runtime_active_model():
    for name in (
        "major_issue.repeat_similarity.yaml",
        "major_issue.repeat_solution.yaml",
        "major_issue.repeat_case.yaml",
    ):
        config = yaml.safe_load(
            (ROOT / "config/runtime/agents" / name).read_text(encoding="utf-8")
        )
        assert "model_ref" not in config
        assert config["metadata"]["model_selection"] == "ACTIVE_MODEL"


def test_candidate_builder_packages_repeat_runtime_and_report_assets():
    source = (
        ROOT / "scripts/build_major_mvp_product_candidate.py"
    ).read_text(encoding="utf-8")
    assert '"data/runtime/.keep"' in source
    assert '"data/repeat_reports/.keep"' in source
    assert '"REPEAT_AGENT_SIMILARITY_M8_2"' in source
    assert '"REPEAT_AGENT_SOLUTION_M8_3"' in source
    assert '"REPEAT_AI_RECOMMENDATION_M8_4_OPTIONAL"' in source
    assert '"REPEAT_MARKDOWN_REPORT"' in source


def test_m83_compact_payload_keeps_typed_semantic_and_exact_evidence():
    context = RepeatAgentAnalysisService._candidate_context(
        "RQ-1",
        _search_result()["query_input"],
        _candidate(),
    )
    payload = RepeatAgentAnalysisService._solution_payload(
        context,
        _similarity_wrapper(),
    )
    assert payload["query"]["problem"] == "当前控制器掉电后启动失败"
    assert payload["historical_case"]["case_id"] == "HCASE-1"
    assert payload["exact_evidence"][0]["raw_text"] == "掉电窗口存在未完成写入。"
    assert "typed_semantic" in payload
    assert "retrieval_document" not in json.dumps(payload, ensure_ascii=False)
    assert "embedding" not in json.dumps(payload, ensure_ascii=False)


def test_m82_m83_execute_through_unified_runtime_provider(tmp_path: Path):
    for relative in (
        "config/runtime/agents/major_issue.repeat_similarity.yaml",
        "config/runtime/agents/major_issue.repeat_solution.yaml",
        "prompts/similarity_analyzer.md",
        "prompts/solution_analyzer.md",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8")

    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    try:
        host, port = server.server_address
        model_config = tmp_path / "config/model.local.yaml"
        model_config.parent.mkdir(parents=True, exist_ok=True)
        model_config.write_text(
            (
                "active_model: repeat_mock\n"
                "models:\n"
                "  repeat_mock:\n"
                "    provider: openai_compatible\n"
                f"    base_url: http://{host}:{port}/v1\n"
                "    api_key: runtime-test-secret\n"
                "    model: repeat-mock-model\n"
                "    temperature: 0\n"
                "    max_tokens: 4096\n"
            ),
            encoding="utf-8",
        )

        service = RepeatAgentAnalysisService(
            tmp_path,
            model_config_path=model_config,
            report_root=tmp_path / "data/repeat_reports",
            decision_enabled=False,
        )
        context = service._candidate_context(
            "RQ-RUNTIME",
            _search_result()["query_input"],
            _candidate(),
        )

        server.state.configure("default", _similarity_analysis(), Behavior())
        similarity, similarity_exec = service._similarity(context)
        assert similarity["analysis_status"] == "SUCCESS"
        assert similarity["analysis"]["overall_score"] == 82
        assert similarity_exec["provider_calls"] == 1
        assert server.state.counters()["default"] == 1

        server.state.configure(
            "default",
            _solution_wrapper()["analysis"],
            Behavior(),
        )
        solution, solution_exec = service._solution(context, similarity)
        assert solution["analysis_status"] == "SUCCESS"
        assert solution["analysis"]["applicability"] == "PARTIAL_REUSE"
        assert solution_exec["provider_calls"] == 1
        assert server.state.counters()["default"] == 1

        assert service.runtime is not None
        assert set(service._resolved) == {
            "major_issue.repeat_similarity",
            "major_issue.repeat_solution",
        }
        assert (
            service._resolved["major_issue.repeat_similarity"].definition.model
            == "repeat-mock-model"
        )
        assert (
            service._resolved["major_issue.repeat_solution"].definition.model
            == "repeat-mock-model"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_product_agent_service_does_not_reconnect_legacy_provider_client():
    source = (
        ROOT / "quality_knowledge/repeat_risk/agent_analysis.py"
    ).read_text(encoding="utf-8")
    assert "ConfiguredAgentRuntime" in source
    assert "OpenAICompatibleClient" not in source
    assert "builder.ai_client" not in source
    assert "builder.repeat_decision" not in source


def test_full_queryinput_restores_organization_and_classification_context():
    trace = {
        "itr_snapshot": {
            "problem_description": "控制器掉电恢复失败",
            "scene": "掉电恢复",
            "product": "PLC",
            "ipmt": "IPMT-A",
            "spdt": "SPDT-A",
            "responsible_department_level2": "研发二部",
            "cause_level1": "软件设计",
            "cause_level2": "资源管理",
            "existing_context": {
                "root_cause": "保存路径存在未完成写入",
                "solution": "增加原子保存",
                "domain": "软件",
            },
        },
        "include_missed_test": False,
        "optional_context": None,
    }
    query = RepeatHistoricalCaseSearchService._to_query_input(trace).to_dict()
    assert query["organization"] == {
        "ipmt": "IPMT-A",
        "spdt": "SPDT-A",
        "responsible_department_level2": "研发二部",
    }
    assert query["classification"] == {
        "cause_level1": "软件设计",
        "cause_level2": "资源管理",
    }


def test_agent_analysis_is_additive_keeps_retrieval_rank_and_delivers_report(
    tmp_path: Path,
    monkeypatch,
):
    service = RepeatAgentAnalysisService(
        tmp_path,
        report_root=tmp_path / "data/repeat_reports",
        decision_enabled=False,
    )
    monkeypatch.setattr(
        service,
        "_similarity",
        lambda context: (_similarity_wrapper(), {"status": "SUCCESS"}),
    )
    monkeypatch.setattr(
        service,
        "_solution",
        lambda context, similarity: (_solution_wrapper(), {"status": "SUCCESS"}),
    )

    enriched = service.analyze(
        {"query_id": "RQ-1"},
        _search_result(),
    )
    candidate = enriched["candidates"][0]

    assert candidate["rank"] == 1
    assert candidate["retrieval_score"] == 0.87
    assert candidate["agent_analysis_status"] == "SUCCESS"
    assert candidate["agent_similarity"]["analysis"]["overall_score"] == 82
    assert candidate["agent_solution"]["analysis"]["applicability"] == "PARTIAL_REUSE"
    assert candidate["ai_recommendation"]["status"] == "DISABLED"
    assert enriched["agent_analysis"]["provider_boundary"] == "UNIFIED_RUNTIME_ONLY"
    assert enriched["analysis_report"]["status"] == "AVAILABLE"

    report_json = tmp_path / enriched["analysis_report"]["report_json_ref"]
    report_markdown = tmp_path / enriched["analysis_report"]["report_markdown_ref"]
    assert report_json.is_file()
    assert report_markdown.is_file()
    report = json.loads(report_json.read_text(encoding="utf-8"))
    assert report["metadata"]["source_artifact"] == "repeat-result/v1:RQ-1"
    assert report["traceability"]["source_artifact"] == "repeat-result/v1:RQ-1"
    assert "AI初步判断" in report["repeat_decision"]["notice"]


def test_typed_semantic_and_exact_evidence_are_agent_inputs(
    tmp_path: Path,
    monkeypatch,
):
    service = RepeatAgentAnalysisService(
        tmp_path,
        report_root=tmp_path / "data/repeat_reports",
        decision_enabled=False,
    )
    search_result = _search_result()
    candidate = search_result["candidates"][0]
    candidate["semantic_mode"] = "TYPED"
    candidate["semantic_contract_version"] = "major-semantic-publish/v1"
    candidate["typed_causes"] = [{
        "semantic_type": "TRC_OCCURRENCE",
        "value": "掉电窗口写入未完成",
        "evidence": [{
            "evidence_id": "E-TYPED-1",
            "source_type": "REPORT",
            "source_id": "ITR-H-1",
            "raw_text": "掉电窗口存在未完成写入。",
        }],
    }]
    candidate["typed_actions"] = [{
        "semantic_type": "CORRECTIVE_ACTION",
        "value": "增加原子保存",
        "evidence": [{
            "evidence_id": "E-TYPED-2",
            "source_type": "REPORT",
            "source_id": "ITR-H-1",
            "raw_text": "采用原子保存机制。",
        }],
    }]
    candidate["semantic_coverage"] = {
        "TRC_OCCURRENCE": "PRESENT",
        "TRC_ESCAPE": "MISSING",
        "MRC_OCCURRENCE": "MISSING",
        "MRC_ESCAPE": "MISSING",
        "TECHNICAL_ACTION": "MISSING",
        "MANAGEMENT_ACTION": "MISSING",
        "CORRECTIVE_ACTION": "PRESENT",
        "PREVENTIVE_ACTION": "MISSING",
    }
    candidate["semantic_evidence_status"] = "COMPLETE"

    captured = {}

    def similarity(context):
        captured["context"] = context
        return _similarity_wrapper(), {"status": "SUCCESS"}

    monkeypatch.setattr(service, "_similarity", similarity)
    monkeypatch.setattr(
        service,
        "_solution",
        lambda context, similarity: (_solution_wrapper(), {"status": "SUCCESS"}),
    )

    enriched = service.analyze({"query_id": "RQ-1"}, search_result)

    typed = captured["context"]["case"]["typed_semantic"]
    assert typed["mode"] == "TYPED"
    assert typed["causes"][0]["semantic_type"] == "TRC_OCCURRENCE"
    assert typed["actions"][0]["semantic_type"] == "CORRECTIVE_ACTION"
    assert captured["context"]["evidence"]["items"][0]["raw_text"] == "掉电窗口存在未完成写入。"
    assert enriched["candidates"][0]["rank"] == 1


def test_m83_mature_topn_policy_does_not_rerank_retrieval(
    tmp_path: Path,
    monkeypatch,
):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "model.yaml").write_text(
        "solution_ai:\n  candidate_top_n: 1\n  min_similarity_score: 0\n"
        "repeat_decision_ai:\n  enabled: false\n",
        encoding="utf-8",
    )
    service = RepeatAgentAnalysisService(
        tmp_path,
        report_root=tmp_path / "data/repeat_reports",
        decision_enabled=False,
    )
    search_result = _search_result()
    first = search_result["candidates"][0]
    second = dict(_candidate())
    second["case_id"] = "HCASE-2"
    second["title"] = "历史案例二"
    second["rank"] = 2
    second["retrieval_score"] = 0.84
    search_result["candidates"] = [first, second]

    def similarity(context):
        wrapper = _similarity_wrapper()
        wrapper["metadata"]["case_id"] = context["case_id"]
        wrapper["analysis"] = dict(wrapper["analysis"])
        wrapper["analysis"]["overall_score"] = (
            75 if context["case_id"] == "HCASE-1" else 92
        )
        return wrapper, {"status": "SUCCESS"}

    solution_calls = []

    def solution(context, similarity_result):
        solution_calls.append(context["case_id"])
        wrapper = _solution_wrapper()
        wrapper["metadata"]["case_id"] = context["case_id"]
        return wrapper, {"status": "SUCCESS"}

    monkeypatch.setattr(service, "_similarity", similarity)
    monkeypatch.setattr(service, "_solution", solution)

    enriched = service.analyze({"query_id": "RQ-1"}, search_result)
    by_id = {item["case_id"]: item for item in enriched["candidates"]}

    assert solution_calls == ["HCASE-2"]
    assert by_id["HCASE-1"]["agent_solution"]["analysis_status"] == "SKIPPED"
    assert by_id["HCASE-2"]["agent_solution"]["analysis_status"] == "SUCCESS"
    assert [(item["case_id"], item["rank"], item["retrieval_score"]) for item in enriched["candidates"]] == [
        ("HCASE-1", 1, 0.87),
        ("HCASE-2", 2, 0.84),
    ]


def test_m84_enabled_is_ai_recommendation_only(
    tmp_path: Path,
    monkeypatch,
):
    service = RepeatAgentAnalysisService(
        tmp_path,
        report_root=tmp_path / "data/repeat_reports",
        decision_enabled=True,
    )
    monkeypatch.setattr(
        service,
        "_similarity",
        lambda context: (_similarity_wrapper(), {"status": "SUCCESS"}),
    )
    monkeypatch.setattr(
        service,
        "_solution",
        lambda context, similarity: (_solution_wrapper(), {"status": "SUCCESS"}),
    )
    recommendation_calls = []

    def recommendation(context, similarity, solution):
        recommendation_calls.append(context["case_id"])
        return {
            "status": "SUCCESS",
            "decision": "LIKELY_REPEAT",
            "confidence": 0.91,
            "decision_reason": "核心机理高度相似但仍需人工确认",
            "evidence_chain": [],
            "key_differences": ["版本不同"],
            "validation_required": ["核对当前根因"],
            "risks": [],
            "recommended_actions": ["人工复核 Evidence"],
        }, {"status": "SUCCESS"}

    monkeypatch.setattr(service, "_recommendation", recommendation)
    enriched = service.analyze({"query_id": "RQ-1"}, _search_result())

    assert recommendation_calls == ["HCASE-1"]
    candidate = enriched["candidates"][0]
    assert candidate["ai_recommendation"]["decision"] == "LIKELY_REPEAT"
    assert enriched["agent_analysis"]["m84_recommendation"] == "ENABLED"
    assert "human_decision" not in enriched


def test_ai_recommendation_never_becomes_human_final_decision(tmp_path: Path):
    repository = RepeatQueryTraceRepository(tmp_path / "repeat.db")
    repository.save({
        "query_id": "RQ-1",
        "subject_ref": "ITR-1",
        "itr_snapshot": {"problem_description": "当前问题"},
        "include_missed_test": False,
        "query_time": "2026-10-06T00:00:00+00:00",
        "algorithm_version": "repeat-risk/v1",
        "correlation_id": "CORR-1",
    })

    search_result = _search_result()
    search_result["candidates"][0]["agent_analysis_status"] = "SUCCESS"
    search_result["candidates"][0]["agent_similarity"] = _similarity_wrapper()
    search_result["candidates"][0]["agent_solution"] = _solution_wrapper()
    search_result["candidates"][0]["ai_recommendation"] = {
        "status": "SUCCESS",
        "decision": "REPEAT_CASE",
        "confidence": 0.94,
        "decision_reason": "AI认为核心机理高度一致",
    }
    search_result["agent_analysis"] = {
        "status": "SUCCESS",
        "m82_similarity": "RESTORED",
        "m83_solution": "RESTORED",
        "m84_recommendation": "ENABLED",
        "provider_boundary": "UNIFIED_RUNTIME_ONLY",
    }
    search_result["analysis_report"] = {
        "status": "AVAILABLE",
        "report_markdown_ref": "data/repeat_reports/RQ-1/report.md",
    }

    result = RepeatResultService(repository).build(search_result)
    assert result["candidates"][0]["ai_recommendation"]["decision"] == "REPEAT_CASE"
    assert result["human_decision"]["decision"] == "PENDING"


def test_runtime_unavailable_preserves_ranked_candidate_and_does_not_auto_decide(
    tmp_path: Path,
    monkeypatch,
):
    service = RepeatAgentAnalysisService(
        tmp_path,
        report_root=tmp_path / "data/repeat_reports",
        decision_enabled=False,
    )

    def unavailable(_context):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(service, "_similarity", unavailable)
    enriched = service.analyze({"query_id": "RQ-1"}, _search_result())
    candidate = enriched["candidates"][0]

    assert candidate["rank"] == 1
    assert candidate["retrieval_score"] == 0.87
    assert candidate["agent_analysis_status"] == "UNAVAILABLE"
    assert candidate["ai_recommendation"]["decision"] is None
    assert enriched["status"] == SEARCH_SUCCESS
    assert enriched["agent_analysis"]["status"] == "UNAVAILABLE"
