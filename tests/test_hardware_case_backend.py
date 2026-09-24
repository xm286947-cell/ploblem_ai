from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from repositories.hardware_case_repository import HardwareCaseRepository
from services.hardware_case_backend import HardwareCaseBackendService
from services.hardware_case_contract import HardwareCaseContractError


def _field(candidate, confirmed=None, disposition="UNREVIEWED", refs=()):
    return {
        "candidate_value": candidate,
        "confirmed_value": confirmed,
        "review_disposition": disposition,
        "evidence_refs": list(refs),
    }


def _case(case_id="HC-M2-001", status="PENDING_REVIEW"):
    return {
        "case_id": case_id,
        "title": "DC/DC 上电异常",
        "case_status": status,
        "processing_status": "READY",
        "source_refs": ["word:A1001.docx"],
        "product_context": {
            "product": "工业控制器",
            "device_name": "电源芯片",
            "device_model": "PMIC-X",
        },
        "facts": {
            "symptom": _field("上电失败"),
            "root_cause": _field("浪涌触发保护"),
            "actions": _field("增加输入保护"),
            "failure_mechanism": _field("输入过压"),
        },
    }


def _node(
    node_id="CF-DC",
    tree_type="CIRCUIT_FEATURE",
    path=None,
    name="DC/DC",
):
    path = path or ["电源", "DC/DC"]
    return {
        "node_id": node_id,
        "tree_type": tree_type,
        "name": name,
        "parent_id": None,
        "path": path,
        "description": None,
        "source_ref": "excel:circuit.xlsx",
        "active": True,
    }


def _mapping(
    case_id="HC-M2-001",
    *,
    mapping_id="MAP-1",
    node_id="CF-DC",
    tree_type="CIRCUIT_FEATURE",
    role="PRIMARY",
    status="CONFIRMED",
):
    return {
        "mapping_id": mapping_id,
        "case_id": case_id,
        "tree_type": tree_type,
        "node_id": node_id,
        "relation_role": role,
        "mapping_status": status,
        "confidence": 0.9,
        "basis_refs": ["EV-1"],
    }


def _evidence(case_id="HC-M2-001", status="AVAILABLE"):
    return {
        "evidence_id": "EV-1",
        "case_id": case_id,
        "source_ref": "word:A1001.docx",
        "evidence_type": "TEXT",
        "locator": {"section": "原因分析", "paragraph": 8},
        "excerpt_or_caption": "浪涌触发保护",
        "evidence_status": status,
    }


def _backend(tmp_path: Path) -> HardwareCaseBackendService:
    return HardwareCaseBackendService(
        HardwareCaseRepository(tmp_path / "hardware_case.sqlite3")
    )


def _review_core(service: HardwareCaseBackendService, case_id="HC-M2-001"):
    service.review_case(
        case_id, "symptom", disposition="CONFIRMED", confirmed_value="上电失败"
    )
    service.review_case(
        case_id,
        "root_cause",
        disposition="CONFIRMED",
        confirmed_value="浪涌触发保护",
    )
    service.review_case(
        case_id, "actions", disposition="CONFIRMED", confirmed_value="增加输入保护"
    )


def test_m2_repository_persists_case_facts_and_source_across_reopen(tmp_path: Path):
    first = _backend(tmp_path)
    first.create_case(_case())
    _review_core(first)

    reopened = _backend(tmp_path)
    case = reopened.get_case("HC-M2-001", role="MAINTAINER")
    assert case["source_refs"] == ["word:A1001.docx"]
    assert case["facts"]["root_cause"]["confirmed_value"] == "浪涌触发保护"


def test_m2_consumer_cannot_read_or_search_unpublished_case(tmp_path: Path):
    service = _backend(tmp_path)
    service.create_case(_case())
    assert service.search_cases("上电失败")["results"] == []
    with pytest.raises(HardwareCaseContractError, match="CASE_NOT_FOUND"):
        service.get_case("HC-M2-001")


def test_m2_publish_cannot_bypass_backend_gate(tmp_path: Path):
    service = _backend(tmp_path)
    service.create_case(_case())
    with pytest.raises(HardwareCaseContractError, match="PUBLISH_GATE_REQUIRED"):
        service.set_case_status("HC-M2-001", "PUBLISHED")

    blocked = service.publish_case("HC-M2-001")
    assert blocked["passed"] is False
    assert set(blocked["blockers"]) == {
        "CORE_FACTS_NOT_REVIEWED",
        "NO_VALID_EVIDENCE",
        "NO_CONFIRMED_MAPPING",
    }


def test_m2_single_confirmed_tree_mapping_is_enough_to_publish(tmp_path: Path):
    service = _backend(tmp_path)
    service.create_case(_case())
    _review_core(service)
    service.save_tree_node(_node())
    service.set_mapping(_mapping())
    service.save_evidence(_evidence())

    result = service.publish_case("HC-M2-001")
    assert result["passed"] is True
    assert result["case_status"] == "PUBLISHED"

    detail = service.get_case("HC-M2-001")
    assert detail["facts"]["root_cause"] == "浪涌触发保护"
    assert detail["circuit_mapping_state"] == "CONFIRMED"
    assert detail["material_mapping_state"] == "UNMAPPED"


def test_m2_material_only_mapping_also_publishes(tmp_path: Path):
    service = _backend(tmp_path)
    service.create_case(_case())
    _review_core(service)
    service.save_tree_node(
        _node(
            node_id="MD-PMIC",
            tree_type="MATERIAL_DEVICE",
            path=["采购件", "IC", "电源管理", "PMIC"],
            name="PMIC",
        )
    )
    service.set_mapping(
        _mapping(
            mapping_id="MAP-M",
            node_id="MD-PMIC",
            tree_type="MATERIAL_DEVICE",
        )
    )
    service.save_evidence(_evidence())
    assert service.publish_case("HC-M2-001")["passed"] is True


def test_m2_multiple_mappings_keep_one_case_and_one_primary_per_tree(tmp_path: Path):
    service = _backend(tmp_path)
    service.create_case(_case(status="PUBLISHED"))
    service.save_tree_node(_node(node_id="CF-A", path=["电源", "A"], name="A"))
    service.save_tree_node(_node(node_id="CF-B", path=["电源", "B"], name="B"))
    service.set_mapping(_mapping(mapping_id="M-A", node_id="CF-A", role="PRIMARY"))
    service.set_mapping(_mapping(mapping_id="M-B", node_id="CF-B", role="PRIMARY"))

    mappings = service.repository.list_mappings(case_id="HC-M2-001")
    assert len(service.repository.list_cases()) == 1
    assert sum(item["relation_role"] == "PRIMARY" for item in mappings) == 1
    assert {item["node_id"] for item in mappings} == {"CF-A", "CF-B"}


def test_mapping_same_semantic_key_is_idempotent_at_service_and_repository(
    tmp_path: Path,
):
    service = _backend(tmp_path)
    service.create_case(_case(status="PUBLISHED"))
    service.save_tree_node(_node(node_id="CF-A", path=["电源", "A"], name="A"))
    first = service.set_mapping(_mapping(mapping_id="M-A", node_id="CF-A"))
    second = service.set_mapping(_mapping(mapping_id="M-A-RETRY", node_id="CF-A"))

    assert second["mapping_id"] == first["mapping_id"] == "M-A"
    assert len(service.repository.list_mappings(case_id="HC-M2-001")) == 1


def test_mapping_semantic_key_allows_other_nodes_and_tree_types(tmp_path: Path):
    service = _backend(tmp_path)
    service.create_case(_case(status="PUBLISHED"))
    service.save_tree_node(_node(node_id="CF-A", path=["电源", "A"], name="A"))
    service.save_tree_node(
        _node(
            node_id="CF-B",
            tree_type="CIRCUIT_FEATURE",
            path=["电源", "B"],
            name="B",
        )
    )
    service.save_tree_node(
        _node(
            node_id="MD-A",
            tree_type="MATERIAL_DEVICE",
            path=["器件", "A"],
            name="A",
        )
    )

    service.set_mapping(_mapping(mapping_id="M-A", node_id="CF-A"))
    service.set_mapping(_mapping(mapping_id="M-B", node_id="CF-B"))
    service.set_mapping(
        _mapping(
            mapping_id="M-M",
            node_id="MD-A",
            tree_type="MATERIAL_DEVICE",
        )
    )

    mappings = service.repository.list_mappings(case_id="HC-M2-001")
    assert len(mappings) == 3
    assert {(item["tree_type"], item["node_id"]) for item in mappings} == {
        ("CIRCUIT_FEATURE", "CF-A"),
        ("CIRCUIT_FEATURE", "CF-B"),
        ("MATERIAL_DEVICE", "MD-A"),
    }


def test_mapping_semantic_key_is_persistence_safe_for_concurrent_retries(
    tmp_path: Path,
):
    db_path = tmp_path / "hardware_case.sqlite3"
    seed = HardwareCaseBackendService(HardwareCaseRepository(db_path))
    seed.create_case(_case(status="PUBLISHED"))
    seed.save_tree_node(_node(node_id="CF-A", path=["电源", "A"], name="A"))

    def submit(retry: int):
        service = HardwareCaseBackendService(HardwareCaseRepository(db_path))
        return service.set_mapping(
            _mapping(mapping_id=f"M-CONCURRENT-{retry}", node_id="CF-A")
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))

    assert len({item["mapping_id"] for item in results}) == 1
    assert len(seed.repository.list_mappings(case_id="HC-M2-001")) == 1


def test_publish_gate_fails_closed_when_fact_evidence_content_mismatches(
    tmp_path: Path,
):
    service = _backend(tmp_path)
    case = _case(status="PENDING_REVIEW")
    for field_name in ("symptom", "root_cause", "actions"):
        case["facts"][field_name]["evidence_refs"] = ["EV-1"]
    service.create_case(case)
    _review_core(service)
    service.save_tree_node(_node())
    service.set_mapping(_mapping())
    service.save_evidence(_evidence())

    gate = service.check_publish_gate("HC-M2-001")

    assert gate["passed"] is False
    assert gate["gate_status"] == "REVIEW_REQUIRED"
    assert gate["publish_blocked"] is True
    assert gate["evidence_validation_status"] == "INVALID"
    assert "EVIDENCE_CONTENT_MISMATCH" in gate["blockers"]
    assert any(
        item["candidate_fact_ref"] == "HC-M2-001:symptom"
        and item["validation"] == "INVALID"
        for item in gate["evidence_validation"]
    )
    assert gate["evidence_traceability"][0]["evidence_ref"] == "EV-1"
    assert gate["evidence_traceability"][0]["source_ref"] == "word:A1001.docx"


def test_publish_gate_accepts_fact_evidence_that_contains_all_confirmed_facts(
    tmp_path: Path,
):
    service = _backend(tmp_path)
    case = _case(status="PENDING_REVIEW")
    for field_name in ("symptom", "root_cause", "actions"):
        case["facts"][field_name]["evidence_refs"] = ["EV-1"]
    service.create_case(case)
    _review_core(service)
    service.save_tree_node(_node())
    service.set_mapping(_mapping())
    evidence = _evidence()
    evidence["excerpt_or_caption"] = "上电失败；浪涌触发保护；增加输入保护"
    service.save_evidence(evidence)

    gate = service.publish_case("HC-M2-001")

    assert gate["passed"] is True
    assert gate["evidence_validation_status"] == "VALID"


def test_publish_gate_fails_closed_when_fact_evidence_cannot_be_resolved(
    tmp_path: Path,
):
    service = _backend(tmp_path)
    case = _case(status="PENDING_REVIEW")
    for field_name in ("symptom", "root_cause", "actions"):
        case["facts"][field_name]["evidence_refs"] = ["EV-MISSING"]
    service.create_case(case)
    _review_core(service)
    service.save_tree_node(_node())
    service.set_mapping(_mapping())

    gate = service.check_publish_gate("HC-M2-001")

    assert gate["passed"] is False
    assert "EVIDENCE_NOT_FOUND" in gate["blockers"]
    assert gate["evidence_validation_status"] == "INVALID"


def test_m2_tree_supports_variable_depth_and_node_query_only_counts_published(tmp_path: Path):
    service = _backend(tmp_path)
    service.create_case(_case(status="PUBLISHED"))
    service.create_case(_case(case_id="HC-DRAFT", status="PENDING_REVIEW"))
    service.save_tree_node(
        _node(
            node_id="MD-WAFER",
            tree_type="MATERIAL_DEVICE",
            path=["采购件", "连接器", "线对板", "信号", "Wafer"],
            name="Wafer",
        )
    )
    service.set_mapping(
        _mapping(
            mapping_id="M-PUB",
            node_id="MD-WAFER",
            tree_type="MATERIAL_DEVICE",
        )
    )
    service.set_mapping(
        _mapping(
            case_id="HC-DRAFT",
            mapping_id="M-DRAFT",
            node_id="MD-WAFER",
            tree_type="MATERIAL_DEVICE",
        )
    )

    tree = service.get_tree("MATERIAL_DEVICE")
    assert len(tree["nodes"][0]["path"]) == 5
    result = service.list_cases_by_tree_node("MD-WAFER")
    assert result["case_count"] == 1
    assert result["results"][0]["case_id"] == "HC-M2-001"


def test_m2_search_covers_confirmed_fact_product_and_tree_path(tmp_path: Path):
    service = _backend(tmp_path)
    service.create_case(_case())
    _review_core(service)
    service.save_tree_node(_node())
    service.set_mapping(_mapping())
    service.save_evidence(_evidence())
    service.publish_case("HC-M2-001")

    assert service.search_cases("浪涌触发保护")["results"][0]["case_id"] == "HC-M2-001"
    assert service.search_cases("工业控制器")["results"][0]["case_id"] == "HC-M2-001"
    assert service.search_cases("电源/DC/DC")["results"][0]["case_id"] == "HC-M2-001"


def test_m2_candidate_value_never_leaks_to_consumer_search_or_detail(tmp_path: Path):
    service = _backend(tmp_path)
    case = _case(status="PUBLISHED")
    service.create_case(case)
    service.save_tree_node(_node())
    service.set_mapping(_mapping())
    service.save_evidence(_evidence())

    detail = service.get_case("HC-M2-001")
    assert detail["facts"]["root_cause"] is None
    assert service.search_cases("浪涌触发保护")["results"] == []


def test_m2_source_unavailable_keeps_published_case_and_creates_anomaly(tmp_path: Path):
    service = _backend(tmp_path)
    service.create_case(_case(status="PUBLISHED"))
    _review_core(service)
    service.save_tree_node(_node())
    service.set_mapping(_mapping())
    service.save_evidence(_evidence(status="SOURCE_UNAVAILABLE"))

    detail = service.get_case("HC-M2-001")
    assert detail["evidence_health"] == "SOURCE_UNAVAILABLE"
    assert service.maintenance_anomalies() == [
        {"case_id": "HC-M2-001", "code": "SOURCE_UNAVAILABLE"}
    ]


def test_m2_maintainer_query_sees_unpublished_consumer_does_not(tmp_path: Path):
    service = _backend(tmp_path)
    service.create_case(_case())
    assert service.search_cases("", role="CONSUMER")["results"] == []
    result = service.search_cases(
        "", role="MAINTAINER", statuses=["PENDING_REVIEW"]
    )
    assert result["results"][0]["case_id"] == "HC-M2-001"


def test_m2_tree_type_mismatch_is_fail_closed(tmp_path: Path):
    service = _backend(tmp_path)
    service.create_case(_case())
    service.save_tree_node(_node())
    with pytest.raises(HardwareCaseContractError, match="TREE_TYPE_MISMATCH"):
        service.set_mapping(
            _mapping(tree_type="MATERIAL_DEVICE", node_id="CF-DC")
        )
