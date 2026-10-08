from __future__ import annotations

from typing import Any

from services.hardware_case_ai_retrieval import (
    HardwareCaseAIRetrievalService,
    understand_hardware_query,
)


def test_w2_synonyms_create_recall_only_variants_without_rewriting_literal() -> None:
    cases = [
        ("单片机这块以前出过什么问题？", "单片机", {"mcu"}),
        ("微控制器有什么历史问题", "微控制器", {"mcu"}),
        ("复位问题", "复位", {"reset", "reset_n"}),
        ("供电问题", "供电", {"电源", "power", "vcc"}),
    ]

    for query, literal, expected_expansions in cases:
        understood = understand_hardware_query(query)
        assert understood["retrieval_text"] == literal
        assert understood["search_queries"][0]["text"] == literal
        variants = understood["search_queries"][1:]
        assert {variant["text"] for variant in variants} == expected_expansions
        assert all(variant["kind"] == "RECALL_ONLY" for variant in variants)
        assert all(
            rule["use"] == "RECALL_ONLY"
            for variant in variants
            for rule in variant["rules"]
        )
        assert all(variant["expansion_cost"] > 0 for variant in variants)

    combined = understand_hardware_query("单片机供电问题")
    combined_texts = {variant["text"] for variant in combined["search_queries"]}
    assert "mcu 供电" in combined_texts
    assert "mcu power" in combined_texts


class FakeCaseService:
    def search_cases(
        self,
        query: str = "",
        *,
        role: str = "CONSUMER",
        statuses: Any = None,
        historical: bool = False,
    ) -> dict[str, Any]:
        return {
            "contract_version": "hardware-case/v1",
            "results": [
                {
                    "case_id": "A0152",
                    "case_status": "PUBLISHED",
                    "title": "MCU reset case",
                    "facts": {"symptom": "MCU 偶发复位"},
                }
            ],
        }


class RecallOnlyFormalSearch:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, text: str = "", *, limit: int = 100, **kwargs):
        self.queries.append(text)
        if text != "mcu":
            return {"results": []}
        return {
            "results": [
                {
                    "knowledge_id": "KO-A0152",
                    "business_case_id": "A0152",
                    "score": 1.0,
                    "why_hit": {"status": "CLAIM_SAFE_MATCH", "reasons": []},
                }
            ]
        }


def test_recall_only_hit_is_explained_and_uses_no_filter_or_write() -> None:
    search = RecallOnlyFormalSearch()
    service = HardwareCaseAIRetrievalService(
        FakeCaseService(),
        consumption_service=search,
    )

    result = service.search_cases("单片机这块以前出过什么问题？")

    assert result["retrieval"]["query_understanding"]["retrieval_text"] == "单片机"
    assert search.queries[0] == "单片机"
    assert "mcu" in search.queries[1:]
    assert [item["case_id"] for item in result["results"]] == ["A0152"]
    why_hit = result["results"][0]["retrieval"]["why_hit"]
    assert why_hit["query_expansion"]["policy"] == "RECALL_ONLY"
    assert why_hit["query_expansion"]["original_query"] == "单片机这块以前出过什么问题？"
    assert why_hit["query_expansion"]["rules"][0]["expanded_term"] == "mcu"
    assert why_hit["query_expansion"]["tier"] == "DIRECT_SYNONYM"
    assert why_hit["query_expansion"]["expansion_cost"] == 1


class MultiCaseService:
    def search_cases(self, query: str = "", **kwargs):
        return {
            "contract_version": "hardware-case/v1",
            "results": [
                {
                    "case_id": "A0152",
                    "case_status": "PUBLISHED",
                    "title": "MCU serial display issue",
                    "facts": {"symptom": "TX output drive is weak"},
                },
                {
                    "case_id": "A0207",
                    "case_status": "PUBLISHED",
                    "title": "Analog reference source deviation",
                    "facts": {"root_cause": "LDO/reference voltage deviation"},
                },
            ],
        }


class PowerExpansionSearch:
    def __init__(self, include_direct_a0152: bool = False) -> None:
        self.include_direct_a0152 = include_direct_a0152

    def search(self, text: str = "", *, limit: int = 100, **kwargs):
        rows = {
            "power": [
                {
                    "business_case_id": "A0207",
                    "knowledge_id": "KO-A0207",
                    "match_score": 0.2,
                    "match_reasons": ["power context"],
                }
            ],
            "vcc": [
                {
                    "business_case_id": "A0152",
                    "knowledge_id": "KO-A0152",
                    "match_score": 99.0,
                    "match_reasons": ["VCC token"],
                }
            ],
        }
        if self.include_direct_a0152:
            rows["电源"] = [
                {
                    "business_case_id": "A0152",
                    "knowledge_id": "KO-A0152",
                    "match_score": 0.1,
                    "match_reasons": ["direct synonym"],
                }
            ]
        return {"results": rows.get(text, [])}


def test_power_results_rank_engineering_alias_before_narrow_vcc_without_filtering():
    service = HardwareCaseAIRetrievalService(
        MultiCaseService(), consumption_service=PowerExpansionSearch()
    )

    result = service.search_cases("供电问题")

    assert [item["case_id"] for item in result["results"]] == ["A0207", "A0152"]
    a0207, a0152 = result["results"]
    assert a0207["retrieval"]["why_hit"]["query_expansion"]["tier"] == "ENGINEERING_ALIAS"
    assert a0152["retrieval"]["why_hit"]["query_expansion"]["tier"] == "NARROW_SIGNAL_ALIAS"
    assert a0152["retrieval"]["why_hit"]["query_expansion"]["expansion_cost"] == 4
    assert a0152["retrieval"]["why_hit"]["query_expansion"]["rules"][0]["expanded_term"] == "vcc"


def test_best_explanation_for_duplicate_case_uses_lower_cost_variant():
    service = HardwareCaseAIRetrievalService(
        MultiCaseService(), consumption_service=PowerExpansionSearch(include_direct_a0152=True)
    )

    result = service.search_cases("供电问题")

    a0152 = next(item for item in result["results"] if item["case_id"] == "A0152")
    expansion = a0152["retrieval"]["why_hit"]["query_expansion"]
    assert expansion["tier"] == "DIRECT_SYNONYM"
    assert expansion["expansion_cost"] == 1
    assert expansion["rules"][0]["expanded_term"] == "电源"
