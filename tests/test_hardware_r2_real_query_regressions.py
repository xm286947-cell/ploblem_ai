"""Regression for actual two-entry 0-hit findings in issue #584."""
from services.hardware_case_ai_retrieval import HardwareCaseAIRetrievalService, understand_hardware_query


class EmptyLocal:
    def search_cases(self, query="", **kwargs):
        return {"contract_version": "hardware-case/v1", "results": []}


class FormalCases:
    def __init__(self):
        self.calls = []
        self.rows = {
            "A0152": {"knowledge_id": "KO-A0152", "business_case_id": "A0152",
                      "title": "CPU串口输出弱上拉导致串口屏乱码", "evidence_refs": ["EV-A0152"]},
            "A0207": {"knowledge_id": "KO-A0207", "business_case_id": "A0207",
                      "title": "模拟量偏差（ADC参考源不准）问题分析报告",
                      "evidence_refs": ["EV-A0207"]},
        }

    def search(self, text="", *, business_case_id=None, limit=100, **kwargs):
        self.calls.append((text, business_case_id))
        if business_case_id:
            row = self.rows.get(business_case_id)
            return {"results": [row] if row else []}
        terms = text.casefold().split()
        return {"results": [
            {**row, "match_score": 1, "match_reasons": [
                {"matched_field": "title", "matched_text": text, "weight": 1},
            ]}
            for row in self.rows.values()
            if all(term in row["title"].casefold() for term in terms)
        ][:limit]}


def test_compound_keeps_strict_all_terms_and_explains_recall():
    plan = understand_hardware_query("串口乱码")
    assert plan["search_queries"][0]["text"] == "串口乱码"
    assert any(x["text"] == "串口 乱码" for x in plan["search_queries"])
    assert all(x["text"] != "串口" for x in plan["search_queries"])
    formal = FormalCases()
    result = HardwareCaseAIRetrievalService(
        EmptyLocal(), consumption_service=formal,
    ).search_cases("串口乱码")
    assert [x["business_case_id"] for x in result["results"]] == ["A0152"]
    assert ("串口 乱码", None) in formal.calls
    expansion = result["results"][0]["retrieval"]["why_hit"]["query_expansion"]
    assert expansion["rules"][0]["rule_id"] == "INTERFACE_SYMPTOM_COMPOUND_SPLIT"


def test_case_number_exact_lookup_and_unknown_number_no_false_hit():
    formal = FormalCases()
    search = HardwareCaseAIRetrievalService(
        EmptyLocal(), consumption_service=formal,
    )
    result = search.search_cases("a0207")
    assert [x["business_case_id"] for x in result["results"]] == ["A0207"]
    assert result["retrieval"]["mode"] == "CASE_ID_EXACT"
    assert ("", "A0207") in formal.calls
    assert search.search_cases("A0999")["results"] == []


def test_negative_reset_does_not_fabricate_knowledge():
    search = HardwareCaseAIRetrievalService(
        EmptyLocal(), consumption_service=FormalCases(),
    )
    assert search.search_cases("复位问题")["results"] == []
