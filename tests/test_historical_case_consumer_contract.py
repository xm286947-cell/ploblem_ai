from __future__ import annotations

import json
from pathlib import Path

import pytest

from repositories import JsonArtifactRepository
from retriever.case_retriever import QueryInput
from services import (
    CONTRACT_VERSION,
    HistoricalCaseConsumerService,
    HistoricalCaseContractError,
)
from services.historical_case_contract import REPEAT_RISK_CONTEXT_CONTRACT_VERSION


def _save_case(
    root: Path,
    case_id: str = "CASE-H-1",
    *,
    root_cause: bool = True,
    solution: bool = True,
    page: int | None = 3,
    extra: dict | None = None,
) -> HistoricalCaseConsumerService:
    repository = JsonArtifactRepository(root)
    repository.save("knowledge/retrieval_docs/CASE-H-1.json", {
        "case_id": case_id,
        "title": "历史控制器重启案例",
        "text": "控制器因报文拥堵重启",
        "source_case_path": "knowledge/enriched_case/CASE-H-1.json",
    })
    analysis = {"root_cause": [{"value": "CAN 接收队列没有流控"}]} if root_cause else {"root_cause": []}
    actions = [{"value": "增加队列水位保护"}] if solution else []
    case = {
        "metadata": {"case_id": case_id, "itr_id": "ITR-H-1", "parse_status": "SUCCESS", "report_filename": "history.pdf"},
        "business_context": {"product": "控制器", "device_type": "PLC", "device_model": "X1"},
        "problem": {
            "standard_description": "控制器因报文拥堵重启",
            "phenomenon": [{"value": "周期性重启"}],
        },
        "analysis": analysis,
        "solution": {"corrective_actions": actions, "preventive_actions": [], "reusable_actions": []},
        "unknown_future_field": extra or {"ignored": True},
    }
    repository.save("knowledge/enriched_case/CASE-H-1.json", case)
    section = {
        "evidence_id": "MJR-EVD-HIST-001",
        "source_type": "REPORT",
        "source_id": "ITR-H-1",
        "source_version": "KREV-H-1",
        "source_ref": "REPORT:ITR-H-1@KREV-H-1",
        "section_type": "root_cause",
        "content": "报告确认 CAN 接收队列没有流控。",
        "page_numbers": [] if page is None else [page],
        "unknown_future_field": "ignored",
    }
    repository.save("knowledge/raw_evidence/CASE-H-1.json", {
        "case_id": case_id,
        "report_filename": "history.pdf",
        "sections": [section],
        "unknown_future_field": "ignored",
    })

    def search(_query: QueryInput, _top_k: int | None) -> dict:
        return {"results": [{
            "case_id": case_id,
            "title": "历史控制器重启案例",
            "score": 0.91,
            "rank": 1,
            "reasons": ["问题现象高度相似"],
            "retrieval_doc_path": "knowledge/retrieval_docs/CASE-H-1.json",
            "internal_path": "/not-for-consumers",
        }]}

    return HistoricalCaseConsumerService(repository, repeat_search=search)


def _save_typed_projection(root: Path, *, missing: set[str] | None = None, corrupt: str | None = None):
    service = _save_case(root)
    repository = JsonArtifactRepository(root)
    missing = missing or set()
    cause_paths = {
        "TRC_OCCURRENCE": ("trc", "occurrence"),
        "TRC_ESCAPE": ("trc", "escape"),
        "MRC_OCCURRENCE": ("mrc", "occurrence"),
        "MRC_ESCAPE": ("mrc", "escape"),
    }
    action_paths = {
        "TECHNICAL_ACTION": "technical_actions",
        "MANAGEMENT_ACTION": "management_actions",
        "CORRECTIVE_ACTION": "corrective_actions",
        "PREVENTIVE_ACTION": "preventive_actions",
    }
    sections = []
    section_sequence = 0

    def item(entry_type: str, value: str, modalities=("PDF",)):
        nonlocal section_sequence
        refs = []
        for index, modality in enumerate(modalities, start=1):
            section_sequence += 1
            evidence_id = f"EVD-{entry_type}-{section_sequence}"
            raw_text = f"原文 {entry_type} {index}"
            refs.append({"source_type": modality, "source_location": f"evidence://{evidence_id}", "quote": raw_text})
            section = {
                "evidence_id": evidence_id,
                "entry_type": entry_type,
                "source_modality": modality,
                "source_type": "MAJOR_SOURCE_DOCUMENT" if modality == "PDF" else "MAJOR_EXCEL_SOURCE_FACT",
                "source_id": f"ITR-H-{entry_type}",
                "source_version": "PUB-REV-1",
                "source_ref": f"SOURCE:ITR-H-{entry_type}@PUB-REV-1",
                "origin_source_id": f"ORIGIN-{entry_type}-{index}",
                "origin_source_version": f"ORIGIN-REV-{index}",
                "origin_source_ref": f"ORIGIN:ID@REV-{index}",
                "file_name": "history.pdf" if modality == "PDF" else "history.xlsx",
                "page": 3 if modality == "PDF" else None,
                "section": entry_type,
                "raw_text": raw_text,
                "url": None,
            }
            if corrupt == "wrong_type" and entry_type == "TRC_OCCURRENCE":
                section["entry_type"] = "TRC_ESCAPE"
            if corrupt == "duplicate_id" and entry_type == "TRC_OCCURRENCE":
                sections.append(dict(section))
            sections.append(section)
        declared = "FUSED" if len(set(modalities)) == 2 else modalities[0]
        return {"value": value, "source_type": declared, "evidence_refs": refs}

    analysis = {"trc": {}, "mrc": {}}
    for entry_type, (family, side) in cause_paths.items():
        if entry_type in missing:
            analysis[family][side] = {"standard": "", "evidence_refs": []}
        else:
            mods = ("EXCEL", "PDF") if entry_type == "TRC_OCCURRENCE" else ("PDF",)
            projected = item(entry_type, f"标准 {entry_type}", mods)
            analysis[family][side] = {
                "standard": projected["value"],
                "evidence_refs": projected["evidence_refs"],
            }
    solution = {}
    for entry_type, field in action_paths.items():
        if entry_type in missing:
            solution[field] = []
        elif entry_type == "CORRECTIVE_ACTION":
            solution[field] = [
                item(entry_type, "纠正措施 A"),
                item(entry_type, "纠正措施 B"),
            ]
        else:
            solution[field] = [item(entry_type, f"措施 {entry_type}")]
    case = {
        "metadata": {
            "case_id": "CASE-H-1",
            "itr_id": "ITR-H-1",
            "semantic_projection_contract": "major-semantic-publish/v1" if corrupt != "unknown_contract" else "major-semantic-publish/v9",
        },
        "business_context": {"product": "控制器"},
        "problem": {"standard_description": "历史问题", "phenomenon": []},
        "analysis": analysis,
        "solution": solution,
        "status": "ACTIVE",
    }
    if corrupt == "quote_mismatch":
        analysis["trc"]["occurrence"]["evidence_refs"][0]["quote"] = "并非原始 Evidence"
    repository.save("knowledge/enriched_case/CASE-H-1.json", case)
    repository.save("knowledge/raw_evidence/CASE-H-1.json", {"case_id": "CASE-H-1", "sections": sections})
    return service


def test_case_01_search_returns_stable_case_id(tmp_path: Path) -> None:
    service = _save_case(tmp_path)
    first = service.search_repeat_cases(QueryInput(text="控制器重启"))
    second = service.search_repeat_cases(QueryInput(text="控制器重启"))
    assert first["contract_version"] == CONTRACT_VERSION
    assert first["candidates"][0]["case_id"] == second["candidates"][0]["case_id"] == "CASE-H-1"


def test_case_02_get_existing_case(tmp_path: Path) -> None:
    detail = _save_case(tmp_path).get_case("CASE-H-1")
    assert detail["case_id"] == "CASE-H-1"
    assert detail["problem_description"] == "控制器因报文拥堵重启"
    assert detail["root_cause"] == "CAN 接收队列没有流控"
    assert detail["solution"] == "增加队列水位保护"


def test_case_03_case_not_found(tmp_path: Path) -> None:
    service = _save_case(tmp_path)
    with pytest.raises(HistoricalCaseContractError, match="CASE_NOT_FOUND") as error:
        service.get_case("CASE-MISSING")
    assert error.value.code == "CASE_NOT_FOUND"


def test_case_04_root_cause_missing_is_not_case_failure(tmp_path: Path) -> None:
    detail = _save_case(tmp_path, root_cause=False).get_case("CASE-H-1")
    assert detail["root_cause"] is None


def test_case_05_solution_missing_is_not_case_failure(tmp_path: Path) -> None:
    detail = _save_case(tmp_path, solution=False).get_case("CASE-H-1")
    assert detail["solution"] is None


def test_case_06_evidence_is_complete_and_traceable(tmp_path: Path) -> None:
    evidence = _save_case(tmp_path).get_case("CASE-H-1")["evidence"]
    assert evidence == [{
        "evidence_id": "MJR-EVD-HIST-001",
        "source_type": "REPORT",
        "source_id": "ITR-H-1",
        "source_version": "KREV-H-1",
        "source_ref": "REPORT:ITR-H-1@KREV-H-1",
        "file_name": "history.pdf",
        "page": 3,
        "section": "root_cause",
        "raw_text": "报告确认 CAN 接收队列没有流控。",
        "url": None,
    }]


def test_case_07_page_unavailable_is_null(tmp_path: Path) -> None:
    evidence = _save_case(tmp_path, page=None).get_case("CASE-H-1")["evidence"]
    assert evidence[0]["page"] is None


def test_case_08_unknown_fields_are_backward_compatible(tmp_path: Path) -> None:
    detail = _save_case(tmp_path, extra={"new_field": "future"}).get_case("CASE-H-1")
    assert "unknown_future_field" not in detail
    assert detail["case_id"] == "CASE-H-1"


def test_case_09_search_candidate_to_detail_round_trip(tmp_path: Path) -> None:
    service = _save_case(tmp_path)
    candidate = service.search_repeat_cases(QueryInput(text="控制器重启"))["candidates"][0]
    detail = service.get_case(candidate["case_id"])
    assert detail["case_id"] == candidate["case_id"]
    assert detail["evidence"]


def test_case_10_consumer_never_needs_internal_path(tmp_path: Path) -> None:
    service = _save_case(tmp_path)
    search = service.search_repeat_cases(QueryInput(text="控制器重启"))
    detail = service.get_case(search["candidates"][0]["case_id"])
    public_payload = json.dumps({"search": search, "detail": detail}, ensure_ascii=False)
    assert "internal_path" not in public_payload
    assert "retrieval_doc_path" not in public_payload
    assert "source_case_path" not in public_payload


def test_repeat_risk_context_is_separate_and_preserves_typed_slots_and_lineage(tmp_path: Path):
    service = _save_typed_projection(tmp_path)
    context = service.get_repeat_risk_context("CASE-H-1")

    assert context["contract_version"] == REPEAT_RISK_CONTEXT_CONTRACT_VERSION
    assert context["semantic_mode"] == "TYPED"
    assert {item["semantic_type"] for item in context["typed_causes"]} == {
        "TRC_OCCURRENCE", "TRC_ESCAPE", "MRC_OCCURRENCE", "MRC_ESCAPE"
    }
    actions = context["typed_actions"]
    assert len(actions) == 5
    correction = [item for item in actions if item["semantic_type"] == "CORRECTIVE_ACTION"]
    assert [item["value"] for item in correction] == ["纠正措施 A", "纠正措施 B"]
    assert correction[0]["evidence"][0]["evidence_id"] != correction[1]["evidence"][0]["evidence_id"]
    fused = next(item for item in context["typed_causes"] if item["semantic_type"] == "TRC_OCCURRENCE")
    assert fused["source_type"] == "FUSED"
    assert {evidence["source_type"] for evidence in fused["evidence"]} == {"EXCEL", "PDF"}
    assert fused["evidence"][0]["origin_source_version"] == "ORIGIN-REV-1"
    historical_v1 = service.get_case("CASE-H-1")
    assert historical_v1["contract_version"] == CONTRACT_VERSION
    assert "typed_causes" not in historical_v1
    assert "semantic_mode" not in historical_v1


def test_repeat_risk_missing_slots_remain_missing_without_inference(tmp_path: Path):
    service = _save_typed_projection(tmp_path, missing={"TRC_ESCAPE", "PREVENTIVE_ACTION"})
    context = service.get_repeat_risk_context("CASE-H-1")

    assert context["semantic_coverage"]["TRC_ESCAPE"] == "MISSING"
    assert context["semantic_coverage"]["PREVENTIVE_ACTION"] == "MISSING"
    assert all(item["semantic_type"] != "TRC_ESCAPE" for item in context["typed_causes"])
    assert all(item["semantic_type"] != "PREVENTIVE_ACTION" for item in context["typed_actions"])


def test_unclassified_source_modality_does_not_invent_excel_pdf_or_fused(tmp_path: Path):
    service = _save_typed_projection(tmp_path)
    repository = JsonArtifactRepository(tmp_path)
    case = repository.load("knowledge/enriched_case/CASE-H-1.json")
    raw = repository.load("knowledge/raw_evidence/CASE-H-1.json")
    ref = case["analysis"]["trc"]["occurrence"]["evidence_refs"][0]
    evidence_id = ref["source_location"].removeprefix("evidence://")
    section = next(item for item in raw["sections"] if item["evidence_id"] == evidence_id)
    section["source_modality"] = "DOCX"
    ref["source_type"] = "DOCX"
    repository.save("knowledge/enriched_case/CASE-H-1.json", case)
    repository.save("knowledge/raw_evidence/CASE-H-1.json", raw)

    context = service.get_repeat_risk_context("CASE-H-1")
    occurrence = next(item for item in context["typed_causes"] if item["semantic_type"] == "TRC_OCCURRENCE")

    assert occurrence["source_type"] is None
    assert occurrence["evidence"][0]["source_type"] == "DOCX"


def test_legacy_case_is_generic_only_and_never_semantically_classified(tmp_path: Path):
    context = _save_case(tmp_path).get_repeat_risk_context("CASE-H-1")

    assert context["semantic_mode"] == "LEGACY_GENERIC_ONLY"
    assert context["typed_causes"] == []
    assert context["typed_actions"] == []
    assert set(context["semantic_coverage"].values()) == {"LEGACY_GENERIC_ONLY"}


@pytest.mark.parametrize("corrupt", ["unknown_contract", "wrong_type", "quote_mismatch", "duplicate_id"])
def test_invalid_typed_contract_or_evidence_fails_closed(tmp_path: Path, corrupt: str):
    service = _save_typed_projection(tmp_path, corrupt=corrupt)

    with pytest.raises(HistoricalCaseContractError) as error:
        service.get_repeat_risk_context("CASE-H-1")

    assert error.value.code in {
        "CASE_SEMANTIC_CONTRACT_UNSUPPORTED",
        "CASE_SEMANTIC_EVIDENCE_INVALID",
    }
