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


class EmptyCaseService(FakeCaseService):
    def __init__(self) -> None:
        self.items = []


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


class FakeAdapter:
    def resolve_evidence(self, evidence_id: str):
        assert evidence_id == "EV-FORMAL-01"
        return {
            "evidence_id": evidence_id,
            "excerpt": "MCU 复位与 RESET_N 瞬态相关",
            "source": {
                "source_id": "SRC-A-MCU-001",
                "uri": "word:A-MCU-001.docx",
                "source_type": "WORD",
                "metadata": {
                    "hardware_locator": {
                        "section": "原因分析",
                        "block_id": "B0008",
                    }
                },
            },
        }


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
    adapter = FakeAdapter()

    def get(self, knowledge_id: str):
        if knowledge_id != "KO-A-MCU-001":
            return None
        return {
            "knowledge_id": "KO-A-MCU-001",
            "business_case_id": "A-MCU-001",
            "title": "MCU intermittent reset",
            "symptom": "MCU 偶发复位，重新上电恢复",
            "root_cause": "RESET_N 受到瞬态干扰",
            "actions": "检查复位信号完整性",
            "evidence_refs": ["EV-FORMAL-01"],
            "formal_revision": 1,
            "formal_object_hash": "a" * 64,
            "projected_at": "2026-10-07T00:00:00+00:00",
        }

    def search(self, text: str = "", *, limit: int = 100, **kwargs):
        if kwargs.get("business_case_id") == "A-MCU-001":
            return {"results": [self.get("KO-A-MCU-001")]}
        if text != "mcu":
            return {"results": []}
        row = self.get("KO-A-MCU-001")
        return {
            "results": [
                {
                    **row,
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
    cases = [
        ("有哪些mcu的问题", "mcu"),
        ("有没有mcu相关案例", "mcu"),
        ("查一下mcu的问题", "mcu"),
        ("mcu有什么历史问题", "mcu"),
        ("单片机这块以前出过什么问题？", "单片机"),
        ("有没有跟 MCU 供电有关的案例？", "mcu 供电"),
    ]
    for query, expected in cases:
        result = understand_hardware_query(query)
        assert result["retrieval_text"] == expected
        assert result["terms"] == expected.split()


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


def test_formal_hit_is_visible_even_when_local_case_table_is_empty() -> None:
    service = HardwareCaseAIRetrievalService(
        EmptyCaseService(),
        retrieval_query_service=FakeQuery(),
        consumption_service=FakeConsumption(),
    )
    result = service.search_cases("有哪些mcu的问题")
    assert [item["case_id"] for item in result["results"]] == ["A-MCU-001"]
    item = result["results"][0]
    assert item["source_kind"] == "FORMAL_KNOWLEDGE"
    assert item["case_status"] == "PUBLISHED"
    assert item["facts"]["symptom"] == "MCU 偶发复位，重新上电恢复"
    assert item["retrieval"]["mode"] == "OPENSEARCH"


def test_formal_projection_fallback_is_visible_without_local_case_or_opensearch() -> None:
    service = HardwareCaseAIRetrievalService(
        EmptyCaseService(),
        consumption_service=FakeConsumption(),
    )
    result = service.search_cases("有哪些mcu的问题")
    assert [item["case_id"] for item in result["results"]] == ["A-MCU-001"]
    assert result["retrieval"]["mode"] == "SQLITE_FORMAL"
    assert result["results"][0]["source_kind"] == "FORMAL_KNOWLEDGE"


def test_formal_only_result_can_open_case_detail() -> None:
    service = HardwareCaseAIRetrievalService(
        EmptyCaseService(),
        consumption_service=FakeConsumption(),
    )
    item = service.get_case("A-MCU-001")
    assert item["source_kind"] == "FORMAL_KNOWLEDGE"
    assert item["title"] == "MCU intermittent reset"


def test_formal_only_detail_resolves_evidence_to_original_source() -> None:
    service = HardwareCaseAIRetrievalService(
        EmptyCaseService(),
        consumption_service=FakeConsumption(),
    )

    payload = service.get_evidence("A-MCU-001")

    assert payload["formal_only"] is True
    assert payload["binding_mode"] == "FORMAL_KNOWLEDGE_DIRECT"
    assert payload["formal_evidence_refs"] == ["EV-FORMAL-01"]
    assert len(payload["evidence"]) == 1
    evidence = payload["evidence"][0]
    assert evidence["evidence_id"] == "EV-FORMAL-01"
    assert evidence["source_id"] == "SRC-A-MCU-001"
    assert evidence["source_ref"] == "word:A-MCU-001.docx"
    assert evidence["locator"]["block_id"] == "B0008"
    assert evidence["evidence_status"] == "AVAILABLE"
