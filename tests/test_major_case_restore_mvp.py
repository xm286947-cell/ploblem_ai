from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from openpyxl import Workbook

from quality_knowledge.major_cases.legacy_adapter import LegacyRepeatAdapter
from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from quality_knowledge.major_cases.restore import MajorCaseRestoreService
from quality_knowledge.major_cases.service import MajorCaseService


ROOT = Path(__file__).resolve().parents[1]


def _workbook(path: Path, rows: list[dict]) -> Path:
    headers = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    wb = Workbook()
    ws = wb.active
    ws.title = "重大问题"
    ws.append(headers)
    for row in rows:
        ws.append([row.get(key, "") for key in headers])
    wb.save(path)
    return path


def _docx(path: Path) -> Path:
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>问题经过</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>设备运行时出现掉电后配置损坏。</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>根因分析</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>配置文件写入过程非原子，掉电窗口触发文件损坏。</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>整改措施</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>采用原子替换并增加掉电测试。</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>验证结果</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>连续掉电验证通过。</w:t></w:r></w:p>'
        '</w:body></w:document>'
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return path


def _env(tmp_path: Path):
    repo = MajorKnowledgeRepository(tmp_path / "knowledge.sqlite3", tmp_path / "attachments")
    service = MajorCaseService(repo)
    restore = MajorCaseRestoreService(repo, ROOT)
    return repo, service, restore


def test_batch_import_without_igr_keeps_source_fact_and_multiple_itrs(tmp_path: Path) -> None:
    repo, service, restore = _env(tmp_path)
    xlsx = _workbook(
        tmp_path / "major.xlsx",
        [{
            "ITR单号": "ITR20260001",
            "关联ITR": "ITR20260002，ITR20260003",
            "问题描述": "PLC运行过程中偶发掉电后配置损坏",
            "IPMT": "工业自动化",
            "SPDT": "PLC",
            "TRC发生": "写入保护不足",
            "MRC发生": "设计检查项缺失",
            "复盘报告": "",
        }],
    )

    preview = restore.stage_upload(
        xlsx.name,
        xlsx.read_bytes(),
        [],
        group_code="G1",
        domain="SOFTWARE",
    )
    assert preview["total"] == 1
    assert preview["rows"][0]["igr"] == ""
    assert preview["rows"][0]["completeness"]["importable"] is True
    assert preview["rows"][0]["completeness"]["retrieval_ready"] is True

    result = restore.commit(preview["batch_id"], service)
    assert result["created_cases"] == 1
    case_id = result["case_ids"][0]
    detail = repo.case_detail(case_id)
    assert {item["standard_itr"] for item in detail["events"]} == {
        "ITR20260001",
        "ITR20260002",
        "ITR20260003",
    }
    assert repo.get_case(case_id)["status"] == "ACTIVE"

    view = restore.feature_view(case_id)
    assert view["case_identity"]["igr"] == ""
    assert view["source_fact_history_count"] == 1
    assert view["effective_features"]["issue_fact"]["source_layer"] == "SOURCE_FACT"
    assert view["effective_features"]["trc_occurrence"]["value"] == "写入保护不足"


def test_batch_import_with_optional_igr_and_review_document(tmp_path: Path) -> None:
    repo, service, restore = _env(tmp_path)
    xlsx = _workbook(
        tmp_path / "major.xlsx",
        [{
            "IGR编号": "IGR-2026-001",
            "ITR单号": "ITR20260011",
            "问题描述": "配置异常",
            "TRC发生": "设计缺陷",
            "复盘报告": "review.docx",
        }],
    )
    docx = _docx(tmp_path / "review.docx")

    preview = restore.stage_upload(
        xlsx.name,
        xlsx.read_bytes(),
        [(docx.name, docx.read_bytes())],
        group_code="G1",
        domain="SOFTWARE",
    )
    row = preview["rows"][0]
    assert row["igr"] == "IGR-2026-001"
    assert row["report_match"]["match_type"] == "EXACT_FILENAME"

    result = restore.commit(preview["batch_id"], service)
    assert result["documents"] == 1
    case_id = result["case_ids"][0]
    identities = restore.identities(case_id)
    assert any(
        item["identity_type"] == "IGR"
        and item["identity_value"] == "IGR-2026-001"
        for item in identities
    )
    assert repo.case_detail(case_id)["documents"][0]["original_filename"] == "review.docx"


def test_feature_resolution_is_field_level_confirmed_then_ai_then_source(tmp_path: Path) -> None:
    repo, service, restore = _env(tmp_path)
    case = service.create_case("字段级融合", "G1", "SOFTWARE")
    event = repo.upsert_event(
        case["case_id"],
        standard_itr="ITR20260021",
        internal_event_key="ITR20260021",
        title="ITR20260021",
    )
    restore.save_source_fact(
        case["case_id"],
        raw={"问题描述": "原始问题描述", "TRC发生": "原始TRC"},
        normalized={
            "original_description": "原始问题描述",
            "trc_occurrence": "原始TRC",
            "product": "PLC",
            "itrs": ["ITR20260021"],
        },
        source_ref="major.xlsx#重大问题:2",
    )
    ai = repo.add_entry(
        case["case_id"],
        "ISSUE_FACT",
        "AI重新理解的问题事实",
        assertion_kind="AI_INFERENCE",
        origin="AI",
        status="PENDING",
        event_id=event["event_id"],
        confidence=0.82,
        explanation="基于复盘材料第3页",
        evidence=[],
    )

    before = restore.feature_view(case["case_id"], event["event_id"])
    assert before["effective_features"]["issue_fact"]["value"] == "AI重新理解的问题事实"
    assert before["effective_features"]["issue_fact"]["source_layer"] == "AI_ANALYSIS"
    assert before["effective_features"]["issue_fact"]["confidence"] == 0.82
    assert before["effective_features"]["trc_occurrence"]["source_layer"] == "SOURCE_FACT"

    service.review_entry(
        ai["entry_id"],
        content="人工确认后的问题事实",
        action="CORRECT",
        reviewer="tester",
        reason="结合现场证据修正",
    )
    after = restore.feature_view(case["case_id"], event["event_id"])
    assert after["effective_features"]["issue_fact"]["value"] == "人工确认后的问题事实"
    assert after["effective_features"]["issue_fact"]["source_layer"] == "CONFIRMED"
    assert after["source_fact"]["normalized"]["original_description"] == "原始问题描述"
    assert after["ai_analysis"]["issue_fact"]["value"] == "AI重新理解的问题事实"


def test_repeat_event_view_consumes_case_feature_view_source_fact(tmp_path: Path) -> None:
    repo, service, restore = _env(tmp_path)
    case = service.create_case("Source Fact Repeat", "G1", "SOFTWARE")
    event = repo.upsert_event(
        case["case_id"],
        standard_itr="ITR20260031",
        internal_event_key="ITR20260031",
        title="ITR20260031",
    )
    restore.save_source_fact(
        case["case_id"],
        raw={},
        normalized={
            "original_description": "电机切换时出现抖动",
            "trc_occurrence": "状态机边界缺失",
            "mrc_occurrence": "设计评审检查项缺失",
            "ipmt": "工业自动化",
            "spdt": "PLC",
            "product": "PLC-X",
            "module": "Motion",
            "itrs": ["ITR20260031"],
        },
        source_ref="major.xlsx#重大问题:2",
    )
    repo.update_case_status(case["case_id"], "ACTIVE")

    repeat = LegacyRepeatAdapter(repo, ROOT, tmp_path / "runs")
    view = repeat.export_event_view(event["event_id"])

    assert view["problem"]["standard_description"] == "电机切换时出现抖动"
    assert view["business_context"]["ipmt"] == "工业自动化"
    assert view["business_context"]["product"] == "PLC-X"
    assert view["analysis"]["trc"]["occurrence"]["standard"] == "状态机边界缺失"
    assert "状态机边界缺失" in view["knowledge"]["retrieval_text"]


def test_source_fact_revision_invalidates_repeat_fingerprint(tmp_path: Path) -> None:
    repo, service, restore = _env(tmp_path)
    case = service.create_case("Fingerprint", "G1", "SOFTWARE")
    event = repo.upsert_event(
        case["case_id"],
        standard_itr="ITR20260041",
        internal_event_key="ITR20260041",
        title="ITR20260041",
    )
    restore.save_source_fact(
        case["case_id"],
        raw={},
        normalized={"original_description": "版本一", "trc_occurrence": "原因一"},
        source_ref="major.xlsx#重大问题:2",
    )
    repeat = LegacyRepeatAdapter(repo, ROOT, tmp_path / "runs")
    first = repeat._knowledge_fingerprint(case["case_id"], event["event_id"])

    restore.save_source_fact(
        case["case_id"],
        raw={},
        normalized={"original_description": "版本二", "trc_occurrence": "原因二"},
        source_ref="major.xlsx#重大问题:2",
    )
    second = repeat._knowledge_fingerprint(case["case_id"], event["event_id"])

    assert first != second
    history = restore.source_fact_history(case["case_id"])
    assert [item["revision_no"] for item in history] == [2, 1]
