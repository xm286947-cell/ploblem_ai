"""Regression for #584 using two Formal-shaped known hardware source rows.

Mocked Agent here is only a unit interface test; it is NOT Real Provider proof.
"""
from services.hardware_knowledge_consumption import HardwareKnowledgeConsumptionService
from services.hardware_r2_query import HardwareR2QueryService


class SnapshotStore:
    def list_all(self):
        return [
            {
                "knowledge_id": "KO-152", "business_case_id": "A0152",
                "title": "CPU_串口输出配置弱上拉导致串口屏乱码",
                "symptom": "MCU给串口屏发送数据时乱码",
                "root_cause": "MCU弱上拉输出电流不足",
                "engineering_rule": "串口TX应确保负载下电平满足VIH",
                "evidence_refs": ["EV152"],
            },
            {
                "knowledge_id": "KO-207", "business_case_id": "A0207",
                "title": "模拟量偏差（ADC参考源不准）问题分析报告",
                "symptom": "模拟量输出精度有问题",
                "root_cause": "REF参考电压异常，运放offset过大",
                "engineering_rule": "模拟量输出精度依赖参考源电压精度",
                "design_constraint": "设计模拟量输出模块时参考源必须满足精度要求",
                "evidence_refs": ["EV207"],
            },
        ]


def svc(agent=None):
    return HardwareR2QueryService(HardwareKnowledgeConsumptionService(SnapshotStore()), agent=agent)


def ids(query, agent=None):
    return [row["business_case_id"] for row in svc(agent).search(query)["results"]]


def test_known_positive_and_negative_queries():
    assert "A0207" in ids("模拟量")
    assert "A0207" in ids("ADC参考源")
    assert "A0207" in ids("模拟量偏差")
    assert "A0152" in ids("串口乱码")
    assert "A0152" in ids("MCU")
    assert ids("A0207") == ["A0207"]
    assert ids("复位问题") == []


def test_natural_language_fallback_is_labelled_deterministic():
    payload = svc().search("设计模拟量电路时，有什么经验可以借鉴？")
    assert [x["business_case_id"] for x in payload["results"]] == ["A0207"]
    assert payload["retrieval"]["mode"] == "DETERMINISTIC"
    assert payload["retrieval"]["agent_status"] == "NOT_CONFIGURED"
    assert payload["results"][0]["match_reasons"]


def test_injected_agent_plan_and_trace_contract_is_not_real_provider():
    def fake(query):
        assert "模拟量" in query
        return (
            {"intent": "DESIGN_REUSE", "normalized_query": "模拟量设计", "query_terms": ["模拟量"]},
            {"task_id": "unit-task", "run_id": "unit-run"},
        )
    response = svc(fake).search("设计模拟量电路时，有什么经验可以借鉴？")
    assert response["retrieval"]["agent_status"] == "COMPLETED"
    assert response["retrieval"]["trace"]["run_id"] == "unit-run"
    assert response["results"][0]["business_case_id"] == "A0207"


def test_failed_provider_is_explicitly_blocked_and_fallback_preserved():
    def failed(_query):
        raise RuntimeError("MODEL_LOCAL_CONFIG_REQUIRED")
    payload = svc(failed).search("设计模拟量电路时，有什么经验可以借鉴？")
    assert payload["retrieval"]["agent_status"] == "BLOCKED"
    assert payload["retrieval"]["mode"] == "DETERMINISTIC"
    assert payload["results"][0]["business_case_id"] == "A0207"


def test_public_v1_search_does_not_silently_change():
    direct = HardwareKnowledgeConsumptionService(SnapshotStore())
    assert direct.search("设计模拟量电路时，有什么经验可以借鉴？")["results"] == []
    assert len(direct.search("模拟量")["results"]) == 1
