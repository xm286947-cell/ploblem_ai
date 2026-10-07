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
