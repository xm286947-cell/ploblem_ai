from __future__ import annotations

from typing import Any

from services.hardware_case_ai_retrieval import (
    HardwareCaseAIRetrievalService,
    HardwareRetrievalCatalogService,
    understand_hardware_query,
)


class FakeCaseError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


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

    def get_case(self, case_id: str, **_kwargs):
        for item in self.items:
            if item["case_id"] == case_id:
                return dict(item)
        raise FakeCaseError("CASE_NOT_FOUND")

    def get_mappings(self, case_id: str, **_kwargs):
        self.get_case(case_id)
        return {"case_id": case_id, "mappings": []}

    def get_evidence(self, case_id: str, **_kwargs):
        self.get_case(case_id)
        return {"case_id": case_id, "evidence": []}


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


FORMAL_PROJECTION = {
    "knowledge_id": "KO-A-MCU-001",
    "public_ref": "hardware-case:A-MCU-001:r1",
    "business_case_id": "A-MCU-001",
    "title": "MCU intermittent reset",
    "symptom": "MCU 偶发复位，重新上电恢复",
    "occurrence_condition": "低压瞬态",
    "failure_mode": "RESET",
    "root_cause": "MCU 供电瞬态跌落",
    "failure_mechanism": "Brownout",
    "analysis_process": "检查电源波形",
    "actions": "增加去耦并改善供电",
    "verification_result": "复测通过",
    "engineering_rule": "检查 MCU 供电裕量",
    "design_constraint": None,
    "diagnostic_clue": "RESET_N",
    "verification_method": "示波器测量",
    "applicability": "MCU",
    "conclusion": "供电瞬态导致复位",
    "interface": None,
    "signal": "RESET_N",
    "key_parameters": [],
    "device_refs": [
        {
            "category": "MCU",
            "generic_name_or_series": "MCU",
            "internal_material_no": None,
            "manufacturer": None,
            "manufacturer_part_no": None,
            "evidence_refs": ["EV-A-MCU-001"],
            "status": "EXPLICIT",
        }
    ],
    "evidence_refs": ["EV-A-MCU-001"],
    "source_domain": "HARDWARE_CASE",
    "source_object_type": "HARDWARE_CASE",
    "formal_revision": 1,
    "formal_status": "ACTIVE",
    "formal_object_hash": "a" * 64,
    "projected_at": "2026-10-07T00:00:00.000+00:00",
}


class FakeAdapter:
    def resolve_evidence(self, evidence_id: str):
        assert evidence_id == "EV-A-MCU-001"
        return {
            "evidence_id": evidence_id,
            "excerpt": "MCU 供电瞬态跌落并触发复位",
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
            return [dict(FORMAL_PROJECTION)]

    store = Store()
    adapter = FakeAdapter()

    def get(self, knowledge_id: str):
        return (
            {"contract_version": "hardware-knowledge-consumption/v1", **FORMAL_PROJECTION}
            if knowledge_id == FORMAL_PROJECTION["knowledge_id"]
            else None
        )

    def search(
        self,
        text: str = "",
        *,
        limit: int = 100,
        business_case_id: str | None = None,
        **_kwargs,
    ):
        if business_case_id:
            if business_case_id != FORMAL_PROJECTION["business_case_id"]:
                return {"results": []}
            return {"results": [dict(FORMAL_PROJECTION)]}
        if text != "mcu":
            return {"results": []}
        return {
            "results": [
                {
                    **FORMAL_PROJECTION,
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


def test_formal_projection_is_sufficient_when_local_case_repository_is_empty() -> None:
    service = HardwareCaseAIRetrievalService(
        EmptyCaseService(),
        consumption_service=FakeConsumption(),
    )

    result = service.search_cases("有哪些mcu的问题")

    assert result["retrieval"]["mode"] == "SQLITE_FORMAL"
    assert [item["case_id"] for item in result["results"]] == ["A-MCU-001"]
    item = result["results"][0]
    assert item["knowledge_id"] == "KO-A-MCU-001"
    assert item["case_status"] == "PUBLISHED"
    assert item["formal_binding"]["mode"] == "FORMAL_KNOWLEDGE_DIRECT"
    assert item["facts"]["root_cause"] == "MCU 供电瞬态跌落"


def test_formal_projection_supports_product_detail_without_local_case_row() -> None:
    service = HardwareCaseAIRetrievalService(
        EmptyCaseService(),
        consumption_service=FakeConsumption(),
    )

    detail = service.get_case("A-MCU-001")
    mappings = service.get_mappings("A-MCU-001")
    evidence = service.get_evidence("A-MCU-001")

    assert detail["knowledge_id"] == "KO-A-MCU-001"
    assert detail["case_status"] == "PUBLISHED"
    assert mappings["binding_mode"] == "FORMAL_KNOWLEDGE_DIRECT"
    assert mappings["mappings"] == []
    assert evidence["binding_mode"] == "FORMAL_KNOWLEDGE_DIRECT"
    assert evidence["evidence"][0]["evidence_id"] == "EV-A-MCU-001"
    assert evidence["evidence"][0]["source_id"] == "SRC-A-MCU-001"
    assert evidence["evidence"][0]["source_ref"] == "word:A-MCU-001.docx"
    assert evidence["evidence"][0]["locator"]["block_id"] == "B0008"


def test_empty_product_listing_includes_formal_published_knowledge() -> None:
    service = HardwareCaseAIRetrievalService(
        EmptyCaseService(),
        consumption_service=FakeConsumption(),
    )

    result = service.search_cases("")

    assert result["retrieval"]["mode"] == "FORMAL_WITH_LEGACY"
    assert [item["case_id"] for item in result["results"]] == ["A-MCU-001"]
