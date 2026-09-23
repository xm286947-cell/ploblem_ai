from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from services.hardware_case_contract import (
    BLOCK_CORE_FACTS,
    BLOCK_EVIDENCE,
    BLOCK_MAPPING,
    CONTRACT_VERSION,
    HardwareCaseContractService,
)


def _field(candidate, confirmed=None, disposition="UNREVIEWED", evidence_refs=()):
    return {
        "candidate_value": candidate,
        "confirmed_value": confirmed,
        "review_disposition": disposition,
        "evidence_refs": list(evidence_refs),
    }


def _case(
    case_id="HC-001",
    *,
    status="PENDING_REVIEW",
    processing_status="READY",
    reviewed=True,
):
    disposition = "CONFIRMED" if reviewed else "UNREVIEWED"
    return {
        "contract_version": CONTRACT_VERSION,
        "case_id": case_id,
        "title": "DC/DC 上电异常",
        "case_status": status,
        "processing_status": processing_status,
        "source_refs": ["word:A0001.docx"],
        "product_context": {
            "product": "工业控制器",
            "device_name": "DC/DC",
            "device_model": "XYZ123",
        },
        "facts": {
            "background": _field("现场上电", "现场上电", disposition),
            "symptom": _field("启动失败", "启动失败", disposition, ["EV-1"]),
            "root_cause": _field("输入浪涌", "输入浪涌", disposition, ["EV-1"]),
            "actions": _field("增加保护", "增加保护", disposition, ["EV-1"]),
            "failure_mechanism": _field("过压触发", "过压触发", disposition),
        },
    }


def _mapping(
    case_id="HC-001",
    *,
    mapping_id="MAP-1",
    tree_type="CIRCUIT_FEATURE",
    node_id="CF-DC",
    node_path="电源/DC-DC",
    status="CONFIRMED",
    role="PRIMARY",
):
    return {
        "mapping_id": mapping_id,
        "case_id": case_id,
        "tree_type": tree_type,
        "node_id": node_id,
        "node_path": node_path,
        "relation_role": role,
        "mapping_status": status,
        "confidence": 0.91,
        "basis_refs": ["EV-1"],
    }


def _evidence(case_id="HC-001", *, status="AVAILABLE"):
    return {
        "evidence_id": "EV-1",
        "case_id": case_id,
        "source_ref": "word:A0001.docx",
        "evidence_type": "TEXT",
        "locator": {"section": "原因分析", "paragraph": 12},
        "excerpt_or_caption": "输入浪涌导致保护触发",
        "evidence_status": status,
    }


def _service(
    *,
    cases=None,
    mappings=None,
    evidence=None,
    trees=None,
):
    return HardwareCaseContractService(
        cases=cases or [_case()],
        mappings=mappings or [],
        evidence=evidence or [],
        trees=trees or [],
    )


def test_schema_accepts_minimal_case_mapping_tree_and_evidence() -> None:
    schema_path = Path(__file__).resolve().parents[1] / "schema" / "hardware_case_contract_v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    resolver = jsonschema.RefResolver.from_schema(schema)

    fixtures = [
        ("CASE", _case()),
        (
            "TREE_NODE",
            {
                "node_id": "N-1",
                "tree_type": "CIRCUIT_FEATURE",
                "name": "DC/DC",
                "parent_id": None,
                "path": ["电源", "DC/DC"],
                "active": True,
            },
        ),
        ("MAPPING", _mapping()),
        ("EVIDENCE", _evidence()),
    ]
    for entity_type, payload in fixtures:
        jsonschema.validate(
            {
                "contract_version": CONTRACT_VERSION,
                "entity_type": entity_type,
                "payload": payload,
            },
            schema,
            resolver=resolver,
        )


def test_ct01_candidate_not_visible_to_consumer_search() -> None:
    result = _service(cases=[_case(status="PENDING_REVIEW")]).search_cases("启动失败")
    assert result["results"] == []


def test_ct02_published_case_visible_in_search_and_tree_query() -> None:
    service = _service(
        cases=[_case(status="PUBLISHED")],
        mappings=[_mapping()],
        evidence=[_evidence()],
    )
    assert service.search_cases("启动失败")["results"][0]["case_id"] == "HC-001"
    tree_result = service.list_cases_by_tree_node("CF-DC")
    assert tree_result["case_count"] == 1
    assert tree_result["results"][0]["case_id"] == "HC-001"


def test_ct03_deprecated_excluded_from_default_consumption() -> None:
    service = _service(
        cases=[_case(status="DEPRECATED")],
        mappings=[_mapping()],
        evidence=[_evidence()],
    )
    assert service.search_cases("")["results"] == []
    assert service.list_cases_by_tree_node("CF-DC")["case_count"] == 0
    assert service.get_case("HC-001", historical=True)["case_status"] == "DEPRECATED"


def test_ct04_circuit_confirmed_material_unmapped_passes_mapping_gate() -> None:
    gate = _service(
        mappings=[_mapping(tree_type="CIRCUIT_FEATURE")],
        evidence=[_evidence()],
    ).check_publish_gate("HC-001")
    assert gate["confirmed_mapping_available"] is True
    assert gate["circuit_mapping_state"] == "CONFIRMED"
    assert gate["material_mapping_state"] == "UNMAPPED"


def test_ct05_material_confirmed_circuit_unmapped_passes_mapping_gate() -> None:
    gate = _service(
        mappings=[
            _mapping(
                tree_type="MATERIAL_DEVICE",
                node_id="MD-1",
                node_path="连接器/板对板",
            )
        ],
        evidence=[_evidence()],
    ).check_publish_gate("HC-001")
    assert gate["confirmed_mapping_available"] is True
    assert gate["material_mapping_state"] == "CONFIRMED"
    assert gate["circuit_mapping_state"] == "UNMAPPED"


def test_ct06_both_trees_unmapped_blocks_publish() -> None:
    gate = _service(evidence=[_evidence()]).publish_case("HC-001")
    assert gate["passed"] is False
    assert BLOCK_MAPPING in gate["blockers"]


def test_ct07_no_available_evidence_blocks_publish() -> None:
    gate = _service(
        mappings=[_mapping()],
        evidence=[_evidence(status="SOURCE_UNAVAILABLE")],
    ).publish_case("HC-001")
    assert gate["passed"] is False
    assert BLOCK_EVIDENCE in gate["blockers"]


def test_ct08_unreviewed_core_fact_blocks_publish() -> None:
    gate = _service(
        cases=[_case(reviewed=False)],
        mappings=[_mapping()],
        evidence=[_evidence()],
    ).publish_case("HC-001")
    assert gate["passed"] is False
    assert BLOCK_CORE_FACTS in gate["blockers"]


def test_ct09_candidate_never_becomes_consumer_effective_value() -> None:
    case = _case(status="PUBLISHED")
    case["facts"]["root_cause"] = _field(
        "AI 猜测根因", confirmed=None, disposition="UNREVIEWED"
    )
    detail = _service(
        cases=[case],
        mappings=[_mapping()],
        evidence=[_evidence()],
    ).get_case("HC-001")
    assert detail["facts"]["root_cause"] is None
    assert "AI 猜测根因" not in repr(detail)


def test_ct10_multiple_mappings_keep_single_case_identity() -> None:
    service = _service(
        cases=[_case(status="PUBLISHED")],
        mappings=[
            _mapping(mapping_id="M1", node_id="CF-1", role="PRIMARY"),
            _mapping(mapping_id="M2", node_id="CF-2", role="SECONDARY"),
            _mapping(
                mapping_id="M3",
                tree_type="MATERIAL_DEVICE",
                node_id="MD-1",
                node_path="器件/电源芯片",
                role="PRIMARY",
            ),
        ],
        evidence=[_evidence()],
    )
    assert len(service.cases) == 1
    assert service.get_case("HC-001")["case_id"] == "HC-001"


def test_ct11_tree_depth_is_variable_not_fixed() -> None:
    service = _service(
        trees=[
            {
                "node_id": "A",
                "tree_type": "CIRCUIT_FEATURE",
                "name": "DC/DC",
                "path": ["电源", "DC/DC"],
                "active": True,
            },
            {
                "node_id": "B",
                "tree_type": "MATERIAL_DEVICE",
                "name": "Wafer",
                "path": ["采购件", "连接器", "线对板", "信号", "Wafer"],
                "active": True,
            },
        ]
    )
    circuit = service.get_tree("CIRCUIT_FEATURE")["nodes"][0]["path"]
    material = service.get_tree("MATERIAL_DEVICE")["nodes"][0]["path"]
    assert len(circuit) == 2
    assert len(material) == 5


def test_ct12_source_unavailable_keeps_published_case_with_warning_and_queue() -> None:
    service = _service(
        cases=[_case(status="PUBLISHED")],
        mappings=[_mapping()],
        evidence=[_evidence(status="SOURCE_UNAVAILABLE")],
    )
    detail = service.get_case("HC-001")
    assert detail["case_status"] == "PUBLISHED"
    assert detail["evidence_health"] == "SOURCE_UNAVAILABLE"
    assert detail["evidence_warning"] == "SOURCE_UNAVAILABLE"
    assert service.maintenance_anomalies() == [
        {"case_id": "HC-001", "code": "SOURCE_UNAVAILABLE"}
    ]


def test_ct13_evidence_locator_traces_source_and_location() -> None:
    service = _service(
        cases=[_case(status="PUBLISHED")],
        mappings=[_mapping()],
        evidence=[_evidence()],
    )
    item = service.get_evidence("HC-001")["evidence"][0]
    assert item["source_ref"] == "word:A0001.docx"
    assert item["locator"]["section"] == "原因分析"
    assert item["locator"]["paragraph"] == 12


def test_ct14_structure_extraction_failure_preserves_source_without_false_confirmed_values() -> None:
    case = _case(processing_status="STRUCTURE_EXTRACTION_FAILED", reviewed=False)
    for field in case["facts"].values():
        field["confirmed_value"] = None
    service = _service(cases=[case])
    maintainer = service.get_case("HC-001", role="MAINTAINER")
    assert maintainer["source_refs"] == ["word:A0001.docx"]
    assert maintainer["processing_status"] == "STRUCTURE_EXTRACTION_FAILED"
    assert all(
        value["confirmed_value"] is None
        for value in maintainer["facts"].values()
    )


def test_ct15_maintainer_can_query_unpublished_consumer_cannot() -> None:
    case = _case(status="PENDING_REVIEW")
    service = _service(cases=[case])
    assert service.search_cases("", role="CONSUMER")["results"] == []
    maintainer = service.search_cases(
        "", role="MAINTAINER", statuses=["PENDING_REVIEW"]
    )
    assert maintainer["results"][0]["case_id"] == "HC-001"
