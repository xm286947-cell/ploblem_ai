from __future__ import annotations

import ast
import json
from pathlib import Path

import jsonschema
import pytest

from services.hardware_case_contract import HardwareCaseContractError, HardwareCaseContractService
from services.hardware_public_consumer import HardwarePublicConsumer, PUBLIC_CONTRACT_VERSION

ROOT = Path(__file__).resolve().parents[1]

def _field(value):
    return {"candidate_value": value, "confirmed_value": value, "review_disposition": "CONFIRMED", "evidence_refs": ["EV-1"]}

def _service(status="PUBLISHED"):
    case = {
        "contract_version": "hardware-case/v1",
        "case_id": "HC-PUBLIC-1",
        "title": "DC/DC 上电异常",
        "case_status": status,
        "processing_status": "READY",
        "source_refs": ["word:A.docx"],
        "product_context": {"product": "工业控制器"},
        "facts": {"symptom": _field("启动失败"), "root_cause": _field("输入浪涌"), "actions": _field("增加保护")},
    }
    mapping = {
        "mapping_id": "MAP-1", "case_id": "HC-PUBLIC-1", "tree_type": "CIRCUIT_FEATURE",
        "node_id": "CF-DC", "node_path": "电源/DC-DC", "relation_role": "PRIMARY",
        "mapping_status": "CONFIRMED", "confidence": 0.9, "basis_refs": ["EV-1"],
    }
    evidence = {
        "evidence_id": "EV-1", "case_id": "HC-PUBLIC-1", "source_ref": "word:A.docx",
        "evidence_type": "TEXT", "locator": {"section": "原因分析", "paragraph": 1},
        "excerpt_or_caption": "输入浪涌导致启动失败，增加保护", "evidence_status": "AVAILABLE",
    }
    tree = {"node_id": "CF-DC", "tree_type": "CIRCUIT_FEATURE", "name": "DC/DC", "path": ["电源","DC/DC"], "active": True}
    return HardwarePublicConsumer(HardwareCaseContractService(cases=[case], mappings=[mapping], evidence=[evidence], trees=[tree]))

def _validate(value):
    schema = json.loads((ROOT / "schema/hardware_public_consumer_v1.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(value, schema)

def test_public_contract_golden_read_path():
    public = _service()
    responses = [
        public.search("启动失败"),
        public.get_case("HC-PUBLIC-1"),
        public.get_tree("CIRCUIT_FEATURE"),
        public.cases_by_tree_node("CF-DC"),
        public.get_mappings("HC-PUBLIC-1"),
        public.get_evidence("HC-PUBLIC-1"),
    ]
    for response in responses:
        assert response["public_contract_version"] == PUBLIC_CONTRACT_VERSION
        _validate(response)
    assert responses[0]["payload"]["results"][0]["case_id"] == "HC-PUBLIC-1"
    assert responses[3]["payload"]["case_count"] == 1
    assert responses[4]["payload"]["mappings"][0]["mapping_status"] == "CONFIRMED"
    assert responses[5]["payload"]["evidence"][0]["source_ref"] == "word:A.docx"

def test_public_contract_fail_closed_for_unpublished_case():
    public = _service(status="PENDING_REVIEW")
    assert public.search("")["payload"]["results"] == []
    with pytest.raises(HardwareCaseContractError, match="CASE_NOT_FOUND"):
        public.get_case("HC-PUBLIC-1")

def test_public_contract_deprecated_requires_explicit_historical():
    public = _service(status="DEPRECATED")
    assert public.search("")["payload"]["results"] == []
    response = public.get_case("HC-PUBLIC-1", historical=True)
    assert response["payload"]["case_status"] == "DEPRECATED"
    _validate(response)

def test_public_consumer_has_no_overall_runtime_or_knowledge_dependency():
    source = (ROOT / "services/hardware_public_consumer.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    forbidden_prefixes = (
        "quality_knowledge.web.p0_app",
        "quality_knowledge.web.api_v2",
        "quality_knowledge.web.repeat_risk_integration",
        "services.historical_case_contract",
        "services.knowledge_service",
        "runtime",
    )
    assert not any(
        module == prefix or module.startswith(prefix + ".")
        for module in imported_modules
        for prefix in forbidden_prefixes
    )
