from __future__ import annotations

from typing import Any

from services.hardware_case_ai_retrieval import (
    HardwareCaseAIRetrievalService,
    HardwareRetrievalCatalogService,
    understand_hardware_query,
)


class FakeCaseService:
    def __init__(self) -> None:
        self.items = [
            {
                "case_id": "A-MCU-001",
                "title": "MCU intermittent reset",
                "case_status": "PUBLISHED",
                "facts": {"symptom": "MCU 偶发复位，重新上电恢复"},
            },
            {
                "case_id": "A-CAN-001",
                "title": "CAN communication interruption",
                "case_status": "PUBLISHED",
                "facts": {"symptom": "CAN 通信偶发中断"},
            },
        ]

    def search_cases(
        self,
        query: str = "",
        *,
        role: str = "CONSUMER",
        statuses: Any = None,
        historical: bool = False,
    ) -> dict[str, Any]:
        q = str(query or "").casefold()
        rows = self.items
        if q:
            rows = [
                item
                for item in rows
                if q
                in (
                    str(item["title"]) + " " + str(item["facts"]["symptom"])
                ).casefold()
            ]
        return {
            "contract_version": "hardware-case/v1",
            "results": [dict(item) for item in rows],
        }


class FakeQuery:
    def search(self, text: str = "", *, filters=None, limit: int = 20):
        assert text == "mcu"
        return {
            "results": [
                {
                    "knowledge_id": "KO-A-MCU-001",
                    "business_case_id": "A-MCU-001",
                    "title": "MCU intermittent reset",
                    "score": 9.5,
                    "why_hit": {
                        "status": "CLAIM_SAFE_MATCH",
                        "claim_safe": True,
                        "reasons": [
                            {
                                "reason_type": "FORMAL_FIELD",
                                "matched_value": "MCU",
                            }
                        ],
                    },
                }
            ]
        }


class BrokenQuery:
    def search(self, text: str = "", *, filters=None, limit: int = 20):
        error = RuntimeError("boom")
        error.code = "SEARCH_ENGINE_UNAVAILABLE"
        raise error


class FakeConsumption:
    class Store:
        def list_all(self):
            return [
                {
                    "knowledge_id": "KO-A-MCU-001",
                    "business_case_id": "A-MCU-001",
                }
            ]

    store = Store()

    def search(self, text: str = "", *, limit: int = 100, **_kwargs):
        if text != "mcu":
            return {"results": []}
        return {
            "results": [
                {
                    "knowledge_id": "KO-A-MCU-001",
                    "business_case_id": "A-MCU-001",
                    "match_score": 100,
                    "match_reasons": [
                        {
                            "matched_field": "title",
                            "matched_text": "mcu",
                            "weight": 100,
                        }
                    ],
                }
            ]
        }


def test_query_understanding_removes_conversational_scaffolding() -> None:
    result = understand_hardware_query("有哪些mcu的问题")
    assert result["retrieval_text"] == "mcu"
    assert result["terms"] == ["mcu"]


def test_product_search_uses_opensearch_and_maps_back_to_published_case() -> None:
    service = HardwareCaseAIRetrievalService(
        FakeCaseService(),
        retrieval_query_service=FakeQuery(),
        consumption_service=FakeConsumption(),
    )
    result = service.search_cases("有哪些mcu的问题")
    assert [item["case_id"] for item in result["results"]] == ["A-MCU-001"]
    assert result["retrieval"]["mode"] == "OPENSEARCH"
    assert result["results"][0]["retrieval"]["knowledge_id"] == "KO-A-MCU-001"


def test_search_engine_failure_degrades_to_formal_projection() -> None:
    service = HardwareCaseAIRetrievalService(
        FakeCaseService(),
        retrieval_query_service=BrokenQuery(),
        consumption_service=FakeConsumption(),
    )
    result = service.search_cases("有哪些mcu的问题")
    assert [item["case_id"] for item in result["results"]] == ["A-MCU-001"]
    assert result["retrieval"]["mode"] == "SQLITE_FORMAL"
    assert result["retrieval"]["degraded"] is True
    assert "SEARCH_ENGINE_UNAVAILABLE" in result["retrieval"]["errors"]


def test_without_search_sidecars_normalized_legacy_search_still_works() -> None:
    service = HardwareCaseAIRetrievalService(FakeCaseService())
    result = service.search_cases("有哪些mcu的问题")
    assert [item["case_id"] for item in result["results"]] == ["A-MCU-001"]
    assert result["retrieval"]["mode"] == "LEGACY_NORMALIZED"
    assert result["retrieval"]["query_understanding"]["retrieval_text"] == "mcu"


def test_catalog_rebuild_tags_projection_then_builds_generation() -> None:
    calls: list[Any] = []

    class Tagger:
        def tag(self, projection):
            calls.append(("tag", projection["knowledge_id"]))
            return {
                "contract_version": "hardware-retrieval-metadata/v1",
                "knowledge_id": projection["knowledge_id"],
            }

    class Indexer:
        def rebuild_all(self, generation_id, items):
            calls.append(("index", generation_id, len(items)))
            return {"generation_id": generation_id, "indexed_count": len(items)}

    catalog = HardwareRetrievalCatalogService(
        FakeConsumption(),
        tagger_factory=Tagger,
        indexer=Indexer(),
    )
    result = catalog.rebuild_all("demo-001")
    assert result["status"] == "PASS"
    assert result["formal_knowledge_write"] is False
    assert result["tagged_count"] == 1
    assert calls == [("tag", "KO-A-MCU-001"), ("index", "demo-001", 1)]
