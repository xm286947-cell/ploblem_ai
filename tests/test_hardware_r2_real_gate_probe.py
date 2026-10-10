"""Safe R2 real-data probe tests; no outbound network or Provider calls."""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from tools import hardware_r2_real_gate_probe as gate


A0207 = {"knowledge_id": "KO-A0207", "business_case_id": "A0207",
         "title": "模拟量偏差（ADC参考源不准）问题分析报告",
         "design_constraint": "参考源电路必须满足精度要求",
         "evidence_refs": ["E-2"]}
A0152 = {"knowledge_id": "KO-A0152", "business_case_id": "A0152",
         "title": "CPU串口乱码",
         "evidence_refs": ["E-1"]}
FLAGS = {"HARDWARE_R2_DEPLOYMENT_MODE": "NON_PROD",
         "HARDWARE_QUERY_AGENT_ENABLED": "1", "HARDWARE_QUERY_AGENT_NONPROD": "1",
         "HARDWARE_CONSUMPTION_AGENT_ENABLED": "1", "HARDWARE_CONSUMPTION_AGENT_NONPROD": "1",
         "HARDWARE_QUERY_AGENT_INTERNAL_TOKEN": "in-memory-query",
         "HARDWARE_ANALYSIS_INTERNAL_TOKEN": "in-memory-analysis"}


def _stub_request(*, invalid_quote=False):
    calls = []

    def request(base, method, path, *, payload=None, token=None, token_header=None, timeout=30):
        calls.append((method, path, token_header))
        if method == "GET" and "/objects/" in path:
            return dict(A0152 if path.endswith("/KO-A0152") else A0207)
        if method == "GET":
            query = parse_qs(urlsplit(path).query)["text"][0]
            if query == "复位问题":
                return {"results": []}
            if query in {"串口乱码", "MCU"}:
                return {"results": [dict(A0152)]}
            return {"results": [dict(A0207)]}
        if path.endswith("/search-assisted"):
            assert token_header == "X-Hardware-Query-Token"
            assert token == FLAGS["HARDWARE_QUERY_AGENT_INTERNAL_TOKEN"]
            return {"results": [dict(A0207)], "retrieval": {
                "query_agent": {"trace": {"agent_id": "hardware_retrieval.query_understand",
                   "task_id": "T01", "run_id": "R01", "provider_calls": 1,
                   "provider": "openai_compatible", "model": "qwen3.8-max"}}}}
        if path.endswith("/analyze"):
            assert token_header == "X-Hardware-Analysis-Token"
            assert token == FLAGS["HARDWARE_ANALYSIS_INTERNAL_TOKEN"]
            quote = "并无依据的设计规格" if invalid_quote else "参考源电路必须满足精度要求"
            return {"knowledge_id": "KO-A0207", "evidence_binding": "CASE_LEVEL_ONLY",
                    "items": [{"source_field": "design_constraint", "source_quote": quote,
                               "suggestion": quote, "advice_scope": "VERBATIM_FORMAL_EXCERPT"}],
                    "trace": {"agent_id": "hardware_retrieval.engineering_consumption",
                              "task_id": "T02", "run_id": "R02", "provider_calls": 1,
                              "provider": "openai_compatible", "model": "qwen3.8-max"}}
        raise AssertionError("Unexpected request")
    return request, calls


def test_only_local_http_host_accepted():
    for invalid in ("https://example.com", "http://192.168.1.10:18785",
                    "http://127.0.0.1:18785/?token=x",
                    "http://user:password@127.0.0.1:18785"):
        with pytest.raises(gate.ProbeError):
            gate.validate_local_base(invalid)
    assert gate.validate_local_base("http://127.0.0.1:18785/") == "http://127.0.0.1:18785"


def test_read_only_probe_checks_known_cases_without_provider(monkeypatch):
    requester, calls = _stub_request()
    monkeypatch.setattr(gate, "_request", requester)
    report = gate.probe("http://127.0.0.1:18785", environ={})
    assert report["deterministic_gate"] == "PASS"
    assert len(report["deterministic_query_results"]) == 8
    assert report["online_agent_gate"] == "NOT_RUN"
    assert report["engineering_agent_gate"] == "NOT_RUN"
    assert report["a0207_formal_identity"]["evidence_ref_count"] == 1
    assert set(report["formal_identity"]) == {"A0152", "A0207"}
    assert report["original_word_sha_gate"] == "NOT_RUN"
    assert all(method == "GET" for method, _, _ in calls)


def test_search_hit_outside_top_five_fails_instead_of_claiming_pass(monkeypatch):
    requester, _ = _stub_request()

    def sixth_hit(*args, **kwargs):
        payload = requester(*args, **kwargs)
        if args[1] == "GET" and "text=%E6%A8%A1%E6%8B%9F%E9%87%8F" in args[2]:
            payload["results"] = [
                {"business_case_id": "A000" + str(i)} for i in range(5)
            ] + [dict(A0207)]
        return payload

    monkeypatch.setattr(gate, "_request", sixth_hit)
    report = gate.probe("http://127.0.0.1:18785", environ={})
    assert report["deterministic_gate"] == "FAIL"
    assert report["deterministic_query_results"][0]["hit_count"] == 6
    assert "A0207" not in report["deterministic_query_results"][0]["actual_business_case_ids"]


def test_missing_formal_evidence_blocks_real_data_gate(monkeypatch):
    requester, _ = _stub_request()

    def missing_evidence(*args, **kwargs):
        result = requester(*args, **kwargs)
        if args[1] == "GET" and args[2].endswith("/KO-A0152"):
            result["evidence_refs"] = []
        return result

    monkeypatch.setattr(gate, "_request", missing_evidence)
    with pytest.raises(gate.ProbeError, match="FORMAL_CASE_EVIDENCE_MISSING_A0152"):
        gate.probe("http://127.0.0.1:18785", environ={})


def test_mock_provider_identity_does_not_count_as_real_agent():
    with pytest.raises(gate.ProbeError, match="REAL_AGENT_PROVIDER_IDENTITY_UNVERIFIED"):
        gate._trace_info({"trace": {
            "task_id": "FAKE_TASK", "run_id": "FAKE_RUN",
            "provider_calls": 1, "provider": "mock_provider", "model": "unit-test",
        }}, analysis=True)


def test_real_agent_approval_precedes_network(monkeypatch):
    monkeypatch.setattr(gate, "_request", lambda *args, **kwargs: pytest.fail("Unexpected network access"))
    with pytest.raises(gate.ProbeError, match="EXPLICIT_REAL_PROVIDER_AUTHORIZATION_REQUIRED"):
        gate.probe("http://127.0.0.1:18785", real_agent=True, environ=FLAGS)
    with pytest.raises(gate.ProbeError, match="NONPROD_AGENT_FLAGS_REQUIRED"):
        gate.probe("http://127.0.0.1:18785", real_agent=True, explicit=True, environ={})
    no_token = {k: v for k, v in FLAGS.items() if k != "HARDWARE_ANALYSIS_INTERNAL_TOKEN"}
    with pytest.raises(gate.ProbeError, match="SERVER_SIDE_AGENT_TOKENS_REQUIRED"):
        gate.probe("http://127.0.0.1:18785", real_agent=True, explicit=True, environ=no_token)


def test_trusted_probe_checks_real_trace_and_grounding_without_leaking_token(monkeypatch):
    requester, calls = _stub_request()
    monkeypatch.setattr(gate, "_request", requester)
    report = gate.probe("http://127.0.0.1:18785", real_agent=True,
                        explicit=True, environ=FLAGS)
    assert report["deterministic_gate"] == "PASS"
    assert report["online_agent_gate"] == "PASS"
    assert report["engineering_agent_gate"] == "PASS"
    assert report["query_agent_trace"]["run_id"] == "R01"
    assert report["engineering_agent_trace"]["run_id"] == "R02"
    assert report["engineering_item_count"] == 1
    assert not any("internal-only" in str(item) for item in report.values())
    assert sum(method == "POST" for method, _, _ in calls) == 2


def test_trusted_probe_fails_closed_on_fabricated_quote(monkeypatch):
    requester, _ = _stub_request(invalid_quote=True)
    monkeypatch.setattr(gate, "_request", requester)
    with pytest.raises(gate.ProbeError, match="ENGINEERING_ANALYSIS_QUOTE_NOT_IN_FORMAL"):
        gate.probe("http://127.0.0.1:18785", real_agent=True,
                   explicit=True, environ=FLAGS)


def test_missing_provider_trace_is_rejected():
    with pytest.raises(gate.ProbeError, match="REAL_AGENT_PROVIDER_TRACE_INCOMPLETE"):
        gate._trace_info({"trace": {"task_id": "MOCK", "run_id": "MOCK",
                                    "provider_calls": 0}}, analysis=True)


def test_native_validation_package_includes_local_real_gate_tool():
    """A ZIP without this probe cannot be used for the promised native gate."""
    from scripts import build_hardware_r1_e2e_validation_package as package
    for relative in ("tools/hardware_r2_real_gate_probe.py",
                     "docs/product/HARDWARE_R2_INTERNAL_AGENT_SECURITY_AND_TRIAL.md"):
        assert relative in package.INCLUDE_FILES
        assert (package.ROOT / relative).is_file()
