from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile
import json
import sqlite3
import threading
import shutil

import pytest

from quality_knowledge.major_cases.backup import create_backup, restore_backup
from quality_knowledge.major_cases.document_parser import parse_document
from quality_knowledge.major_cases.legacy_adapter import LegacyRepeatAdapter
from quality_knowledge.major_cases.legacy_import import LegacyCaseImporter
from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from quality_knowledge.major_cases.service import MajorCaseService
from quality_knowledge.major_cases.sources import SqliteBusinessSourceGateway
from builder.ai_client import AIClientError, AIResponse


ROOT = Path(__file__).resolve().parents[1]


def test_m0_legacy_m6_builds_only_in_isolated_root(tmp_path: Path) -> None:
    from builder.m6_runner import run_m6

    for relative in (
        "config/app.yaml", "config/model.yaml", "schema/retrieval_document.schema.json",
        "schema/embedding_record.schema.json", "schema/knowledge_manifest.schema.json",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    source = tmp_path / "knowledge/enriched_case/CASE-SYNTH.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "tests/samples/valid_standard_case.json", source)
    result = run_m6(tmp_path, case_id="CASE-SYNTH", overwrite=True)
    assert result["failed_count"] == 0 and result["document_count"] == 1
    assert (tmp_path / "knowledge/index/case_index.jsonl").exists()


def _docx(path: Path, sections: list[tuple[str, list[str]]], table: list[list[str]] | None = None) -> Path:
    paragraphs = []
    for heading, bodies in sections:
        paragraphs.append(
            f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>{heading}</w:t></w:r></w:p>'
        )
        paragraphs.extend(f"<w:p><w:r><w:t>{body}</w:t></w:r></w:p>" for body in bodies)
    table_xml = ""
    if table:
        rows = []
        for row in table:
            cells = "".join(f"<w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc>" for cell in row)
            rows.append(f"<w:tr>{cells}</w:tr>")
        table_xml = "<w:tbl>" + "".join(rows) + "</w:tbl>"
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        + "".join(paragraphs) + table_xml + "</w:body></w:document>"
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return path


def _business_db(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """CREATE TABLE business_event(
                 record_id TEXT PRIMARY KEY,group_code TEXT NOT NULL,standard_itr TEXT NOT NULL,
                 source_type TEXT NOT NULL,version_no INTEGER NOT NULL,title TEXT);
               INSERT INTO business_event VALUES
                 ('B1','G1','ITR20260001','ITR',1,'电机异常'),
                 ('B2','G1','ITR20260002','ITR',1,'通信异常'),
                 ('B3','G2','ITR20260001','ITR',1,'隔离分组数据');"""
        )
    return path


@pytest.fixture
def env(tmp_path: Path):
    business = _business_db(tmp_path / "business.sqlite3")
    repo = MajorKnowledgeRepository(tmp_path / "knowledge.sqlite3", tmp_path / "attachments")
    service = MajorCaseService(repo, SqliteBusinessSourceGateway(business))
    return tmp_path, business, repo, service


def _full_review(path: Path, *, historical: bool = False, long: bool = False) -> Path:
    suffix = "；历史参考 ITR20269999" if historical else ""
    huge = "背景说明" * 7000 if long else ""
    return _docx(path, [
        ("问题经过", [f"本案 ITR20260001 在运行中出现电机抖动{suffix}。{huge}"]),
        ("根因分析", ["根因是状态机边界判断缺失，导致切换竞争。"]),
        ("整改措施", ["措施是修正状态机并增加边界条件自动化测试。"]),
        ("验证结果", ["验证结果显示连续运行72小时未复现。"]),
    ], table=[["项目", "结论"], ["回归", "通过"]])


def test_independent_schema_docx_dedup_versions_multi_itr_and_group_isolation(env) -> None:
    tmp, business, repo, service = env
    case = service.create_case("重大复盘", "G1")
    document = _full_review(tmp / "review.docx", historical=True)
    before = business.read_bytes()
    first = service.ingest(case["case_id"], document, current_itrs=["itr20260001", "ITR20260002"])
    assert first["action"] == "NEW"
    assert first["fragment_count"] == 5
    assert first["association"] == {"linked": 2, "pending": 0, "conflicts": 0, "events": 2, "historical_references": 1}
    second = service.ingest(case["case_id"], document, current_itrs=["ITR20260001"])
    assert second["action"] == "SKIPPED"
    assert business.read_bytes() == before
    detail = service.detail(case["case_id"])
    assert {event["standard_itr"] for event in detail["events"]} == {"ITR20260001", "ITR20260002"}
    assert not any("," in event["standard_itr"] for event in detail["events"])
    historical = [link for link in detail["source_links"] if link["relation_role"] == "HISTORICAL_REFERENCE"]
    assert historical[0]["standard_itr"] == "ITR20269999"
    other = service.create_case("同文不同组", "G2")
    isolated = service.ingest(other["case_id"], document, current_itrs=["ITR20260001"])
    assert isolated["action"] == "NEW"
    assert isolated["version_id"] != first["version_id"]
    changed = _full_review(tmp / "review-v2.docx")
    with ZipFile(changed, "a") as archive:
        archive.writestr("custom/revision.txt", "v2")
    version = service.ingest(case["case_id"], changed, current_itrs=["ITR20260001"], document_id=first["document_id"])
    assert version["action"] == "UPDATED" and version["version_no"] == 2


def test_no_itr_scan_warning_and_legacy_doc_message(env) -> None:
    tmp, _, repo, service = env
    case = service.create_case("无ITR", "G1")
    document = _docx(tmp / "no-itr.docx", [("问题经过", ["现场出现未知异常。"])])
    result = service.ingest(case["case_id"], document)
    assert result["association"]["pending"] == 1
    assert repo.events(case["case_id"])[0]["standard_itr"] == ""
    old_doc = tmp / "legacy.doc"
    old_doc.write_bytes(b"legacy word")
    old = service.ingest(case["case_id"], old_doc)
    assert old["parse_status"] == "FAILED"
    assert old["warnings"] == ["LEGACY_DOC_UNSUPPORTED_SAVE_AS_DOCX"]
    blank = tmp / "blank.pdf"
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with blank.open("wb") as handle:
        writer.write(handle)
    parsed = parse_document(blank)
    assert "SCANNED_PDF_SUSPECTED" in parsed.warnings


def test_skill_budget_missing_retry_human_revision_and_skill_version(env) -> None:
    tmp, _, repo, service = env
    case = service.create_case("长文复盘", "G1")
    result = service.ingest(case["case_id"], _full_review(tmp / "long.docx", long=True), current_itrs=["ITR20260001"])
    extraction = service.extract(case["case_id"], result["version_id"])
    assert extraction["state"] == "PARTIAL" and extraction["truncated"] is True
    first_run = repo.run(extraction["run_id"])
    assert first_run["state"] == "PARTIAL"
    assert 0 < first_run["coverage_processed"] < first_run["coverage_total"]

    continued = service.extract(case["case_id"], result["version_id"])
    assert continued["run_id"] == extraction["run_id"]
    assert continued["continued"] is True
    assert continued["reused"] is False
    assert continued["state"] == "COMPLETED"
    run = repo.run(extraction["run_id"])
    assert run["state"] == "COMPLETED"
    assert run["coverage_processed"] == run["coverage_total"]
    assert run["model_profile"] == "programmatic-mock"
    assert run["error_code"] == ""
    entries = repo.entries(case["case_id"])
    assert {item["entry_type"] for item in entries} == {"ISSUE_FACT", "ROOT_CAUSE", "ACTION", "VERIFICATION"}
    target = next(item for item in entries if item["entry_type"] == "ROOT_CAUSE")
    original_evidence = target["evidence"]
    assert original_evidence
    revised = service.review_entry(target["entry_id"], content="人工确认：状态机边界判断缺失。", action="CORRECT", reviewer="tester", reason="对照复盘原文")
    assert revised["status"] == "CORRECTED" and revised["origin"] == "HUMAN"
    assert revised["evidence"]
    assert [
        (item["fragment_id"], item["source_link_id"], item["locator"], item["excerpt"])
        for item in revised["evidence"]
    ] == [
        (item["fragment_id"], item["source_link_id"], item["locator"], item["excerpt"])
        for item in original_evidence
    ]
    assert revised["current_revision_id"] != target["current_revision_id"]
    base = service.skill_runner.ensure_default_skill()
    config = {
        "skill_code": "major_review_extract", "material_types": ["DOCX"],
        "required_sections": [{"entry_type": "ISSUE_FACT", "labels": ["问题经过"]}],
        "auxiliary_sections": [], "output_schema": {"type": "array"}, "prompt_rules": "只读指定片段",
        "tag_dictionary": {}, "input_budget": 1000, "output_budget": 200,
    }
    newer = repo.seed_skill(config)
    assert newer["version"] == base["version"] + 1
    assert repo.skill(base["skill_version_id"])["config_hash"] == base["config_hash"]

    missing_case = service.create_case("无根因", "G1")
    minimal = _docx(tmp / "minimal.docx", [("问题经过", ["仅记录了现象。"]), ("整改措施", ["重启。"])])
    imported = service.ingest(missing_case["case_id"], minimal)
    service.extract(missing_case["case_id"], imported["version_id"])
    missing = [item for item in repo.entries(missing_case["case_id"]) if item["status"] == "MISSING"]
    assert {item["entry_type"] for item in missing} >= {"ROOT_CAUSE", "VERIFICATION"}

    queued, _ = repo.create_or_reuse_run(missing_case["case_id"], imported["version_id"], "MANUAL_TEST", "cancel-resume")
    cancelled = repo.cancel_run(queued["run_id"])
    assert cancelled["state"] == "CANCELLED"
    repo.set_run(queued["run_id"], "RUNNING")
    assert repo.run(queued["run_id"])["state"] == "RUNNING"



class _FakeSkillModelClient:
    def __init__(self, payload: dict, model: str = "fake-real-model") -> None:
        self.payload = payload
        self.model = model
        self.calls: list[list[dict]] = []

    def complete(self, messages: list[dict]) -> AIResponse:
        self.calls.append(messages)
        return AIResponse(
            content=json.dumps(self.payload, ensure_ascii=False),
            model=self.model,
            raw={"fake": True},
        )


class _FailingSkillModelClient:
    model = "fake-failing-model"

    def complete(self, messages: list[dict]) -> AIResponse:
        raise AIClientError("synthetic model outage")


def _real_model_payload(repo: MajorKnowledgeRepository, version_id: str, *, invalid_evidence: bool = False) -> dict:
    fragments = repo.fragments(version_id)
    by_section = {item["section_path"]: item["fragment_id"] for item in fragments}
    def ref(label: str) -> list[str]:
        if invalid_evidence:
            return ["UNKNOWN-FRAGMENT"]
        match = next((fragment_id for section, fragment_id in by_section.items() if label in section), "")
        return [match] if match else []
    return {
        "entries": [
            {"entry_type": "ISSUE_FACT", "content": "模型识别：电机运行中出现抖动。", "fragment_ids": ref("问题经过")},
            {"entry_type": "ROOT_CAUSE", "content": "模型识别：状态机边界判断缺失导致竞争。", "fragment_ids": ref("根因")},
            {"entry_type": "ACTION", "content": "模型识别：修正状态机并增加自动化测试。", "fragment_ids": ref("整改")},
            {"entry_type": "VERIFICATION", "content": "模型识别：连续运行验证未复现。", "fragment_ids": ref("验证")},
        ]
    }


def test_skill_real_model_path_is_executed_and_evidence_is_program_bound(env) -> None:
    tmp, _, repo, _ = env
    case = MajorCaseService(repo).create_case("真实模型执行", "G1")
    setup = MajorCaseService(repo)
    imported = setup.ingest(case["case_id"], _full_review(tmp / "real-model.docx"), current_itrs=["ITR20260001"])
    client = _FakeSkillModelClient(_real_model_payload(repo, imported["version_id"]))
    service = MajorCaseService(repo, skill_model_client=client)

    result = service.extract(case["case_id"], imported["version_id"], execution_mode="real")
    assert result["state"] == "COMPLETED"
    assert result["execution_outcome"] == "REAL_MODEL"
    assert result["model_profile"] == "real-model:fake-real-model"
    assert len(client.calls) == 1

    run = repo.run(result["run_id"])
    assert run["model_profile"] == "real-model:fake-real-model"
    assert run["error_code"] == ""
    entries = repo.entries(case["case_id"])
    assert all(item["model_profile"] == "real-model:fake-real-model" for item in entries if item["status"] == "PENDING")
    assert all(item["evidence"] for item in entries if item["status"] == "PENDING")
    valid_fragment_ids = {item["fragment_id"] for item in repo.fragments(imported["version_id"])}
    assert {
        evidence["fragment_id"]
        for item in entries
        for evidence in item["evidence"]
    } <= valid_fragment_ids


def test_skill_real_model_evidence_insufficient_is_explicit(env) -> None:
    tmp, _, repo, _ = env
    setup = MajorCaseService(repo)
    case = setup.create_case("模型证据不足", "G1")
    imported = setup.ingest(case["case_id"], _full_review(tmp / "insufficient.docx"), current_itrs=["ITR20260001"])
    client = _FakeSkillModelClient(_real_model_payload(repo, imported["version_id"], invalid_evidence=True))
    service = MajorCaseService(repo, skill_model_client=client)

    result = service.extract(case["case_id"], imported["version_id"], execution_mode="real")
    assert result["state"] == "COMPLETED"
    assert result["execution_outcome"] == "EVIDENCE_INSUFFICIENT"
    run = repo.run(result["run_id"])
    assert run["error_code"] == "EVIDENCE_INSUFFICIENT"
    assert all(item["status"] == "MISSING" for item in repo.entries(case["case_id"]))


def test_skill_real_model_failure_never_falls_back_to_mock(env) -> None:
    tmp, _, repo, _ = env
    setup = MajorCaseService(repo)
    case = setup.create_case("模型失败", "G1")
    imported = setup.ingest(case["case_id"], _full_review(tmp / "model-failed.docx"), current_itrs=["ITR20260001"])
    service = MajorCaseService(repo, skill_model_client=_FailingSkillModelClient())

    result = service.extract(case["case_id"], imported["version_id"], execution_mode="real")
    assert result["state"] == "FAILED"
    assert result["execution_outcome"] == "MODEL_FAILED"
    assert result["model_profile"] == "real-model:fake-failing-model"
    run = repo.run(result["run_id"])
    assert run["error_code"] == "MODEL_FAILED"
    assert repo.entries(case["case_id"]) == []


def test_skill_mock_execution_remains_explicit(env) -> None:
    tmp, _, repo, service = env
    case = service.create_case("Mock模型", "G1")
    imported = service.ingest(case["case_id"], _full_review(tmp / "mock-explicit.docx"), current_itrs=["ITR20260001"])
    result = service.extract(case["case_id"], imported["version_id"], execution_mode="mock")
    assert result["execution_outcome"] == "MOCK"
    assert result["model_profile"] == "programmatic-mock"
    assert repo.run(result["run_id"])["model_profile"] == "programmatic-mock"


def test_source_conflict_stale_and_deleted(env) -> None:
    tmp, business, repo, service = env
    case = service.create_case("来源对账", "G1")
    imported = service.ingest(case["case_id"], _full_review(tmp / "source.docx"), current_itrs=["ITR20260001"])
    assert imported["association"]["linked"] == 1
    with sqlite3.connect(business) as connection:
        connection.execute("UPDATE business_event SET version_no=2 WHERE record_id='B1'")
    assert service.reconcile(case["case_id"])["stale"] == 1
    with sqlite3.connect(business) as connection:
        connection.execute("DELETE FROM business_event WHERE record_id='B1'")
    assert service.reconcile(case["case_id"])["unavailable"] == 1
    with sqlite3.connect(business) as connection:
        connection.execute("INSERT INTO business_event VALUES('B4','G1','ITR20260002','ITR',1,'重复命中')")
    conflict_case = service.create_case("冲突", "G1")
    conflict = service.ingest(conflict_case["case_id"], _full_review(tmp / "conflict.docx"), current_itrs=["ITR20260002"])
    assert conflict["association"]["conflicts"] == 1


def _active_case(service: MajorCaseService, repo: MajorKnowledgeRepository, tmp: Path, title: str, itr: str, suffix: str) -> tuple[dict, dict]:
    case = service.create_case(title, "G1")
    result = service.ingest(case["case_id"], _full_review(tmp / f"{suffix}.docx"), current_itrs=[itr])
    service.extract(case["case_id"], result["version_id"])
    for entry in repo.entries(case["case_id"]):
        if entry["status"] != "MISSING":
            service.review_entry(entry["entry_id"], content=entry["content"], action="CONFIRM", reviewer="tester")
    repo.update_case_status(case["case_id"], "ACTIVE")
    return case, repo.events(case["case_id"])[0]


def _active_case_with_review(
    service: MajorCaseService,
    repo: MajorKnowledgeRepository,
    tmp: Path,
    title: str,
    itr: str,
    suffix: str,
    *,
    problem: str,
    cause: str,
    action: str,
) -> tuple[dict, dict]:
    case = service.create_case(title, "G1")
    document = _docx(tmp / f"{suffix}.docx", [
        ("问题经过", [problem]),
        ("根因分析", [cause]),
        ("整改措施", [action]),
        ("验证结果", ["验证通过，问题未复现。"]),
    ])
    result = service.ingest(case["case_id"], document, current_itrs=[itr])
    service.extract(case["case_id"], result["version_id"])
    for entry in repo.entries(case["case_id"]):
        if entry["status"] != "MISSING":
            service.review_entry(entry["entry_id"], content=entry["content"], action="CONFIRM", reviewer="tester")
    repo.update_case_status(case["case_id"], "ACTIVE")
    return case, repo.events(case["case_id"])[0]


def test_repeat_candidates_use_similarity_not_recency_and_preserve_group_isolation(env) -> None:
    tmp, _, repo, service = env
    similar_case, similar_event = _active_case_with_review(
        service, repo, tmp, "历史相似案例", "ITR20260002", "similar",
        problem="电机在高速切换过程中出现明显抖动。",
        cause="根因是状态机边界判断缺失，切换过程发生竞争。",
        action="修正状态机边界并补充切换场景自动化测试。",
    )
    unrelated_case, unrelated_event = _active_case_with_review(
        service, repo, tmp, "近期无关案例", "ITR20260003", "unrelated",
        problem="通信报文周期性丢失并出现网络断连。",
        cause="根因是连接器松动导致物理链路不稳定。",
        action="重新锁紧连接器并增加振动检查。",
    )
    current_case, current_event = _active_case_with_review(
        service, repo, tmp, "当前案例", "ITR20260001", "current-similarity",
        problem="电机高速切换时出现抖动。",
        cause="根因是状态机边界竞争。",
        action="修正状态机并增加边界测试。",
    )
    with repo.connect() as connection:
        connection.execute("UPDATE kb_case SET updated_at='2026-01-01 00:00:00' WHERE case_id=?", (similar_case["case_id"],))
        connection.execute("UPDATE kb_case SET updated_at='2026-01-02 00:00:00' WHERE case_id=?", (unrelated_case["case_id"],))

    other_group = service.create_case("其他分组相似案例", "G2")
    other_event = repo.upsert_event(other_group["case_id"], standard_itr="ITR20260009", internal_event_key="ITR20260009")
    repo.update_case_status(other_group["case_id"], "ACTIVE")

    adapter = LegacyRepeatAdapter(repo, ROOT, tmp / "retrieval-runs")
    ranked = adapter._candidate_events(current_event, limit=1)
    assert ranked[0]["event_id"] == similar_event["event_id"]
    assert ranked[0]["retrieval_score"] > 0
    assert all(item["event_id"] != other_event["event_id"] for item in ranked)
    assert all(item["case_id"] != current_case["case_id"] for item in ranked)


def test_repeat_cache_invalidates_when_candidate_knowledge_revision_changes(env) -> None:
    tmp, _, repo, service = env
    historical, historical_event = _active_case_with_review(
        service, repo, tmp, "历史候选", "ITR20260002", "cache-history",
        problem="电机高速切换时出现抖动。",
        cause="根因是状态机边界竞争。",
        action="修正状态机并增加边界测试。",
    )
    _, current_event = _active_case_with_review(
        service, repo, tmp, "当前问题", "ITR20260001", "cache-current",
        problem="电机高速切换时出现抖动。",
        cause="根因是状态机边界竞争。",
        action="修正状态机并增加边界测试。",
    )
    adapter = LegacyRepeatAdapter(repo, ROOT, tmp / "cache-runs")
    first = adapter.run(current_event["event_id"], mock=True, limit=1)
    second = adapter.run(current_event["event_id"], mock=True, limit=1)
    assert second["reused"] is True
    assert second["run_id"] == first["run_id"]

    root_cause = next(item for item in repo.entries(historical["case_id"]) if item["entry_type"] == "ROOT_CAUSE")
    service.review_entry(
        root_cause["entry_id"],
        content=root_cause["content"] + " 人工补充：已确认竞争窗口。",
        action="CORRECT",
        reviewer="tester",
        reason="候选知识修订",
    )

    refreshed = adapter.run(current_event["event_id"], mock=True, limit=1)
    assert refreshed["reused"] is False
    assert refreshed["run_id"] != first["run_id"]
    assert refreshed["candidate_count"] == 1
    assert repo.run(first["run_id"])["state"] == "COMPLETED"


def test_legacy_m8_isolated_repeat_and_same_case_exclusion(env) -> None:
    tmp, _, repo, service = env
    historical, historical_event = _active_case(service, repo, tmp, "历史案例", "ITR20260002", "history")
    current, current_event = _active_case(service, repo, tmp, "当前案例", "ITR20260001", "current")
    # A sibling event in the same case must not become a repeat candidate because
    # the extracted review conclusions are case-level and cannot be safely copied.
    repo.upsert_event(current["case_id"], standard_itr="ITR20260003", internal_event_key="ITR20260003")
    runs = tmp / "isolated-runs"
    adapter = LegacyRepeatAdapter(repo, ROOT, runs)
    result = adapter.run(current_event["event_id"], mock=True)
    assert result["state"] == "COMPLETED"
    assert result["candidate_count"] == 1
    assert Path(result["run_directory"]).is_relative_to(runs)
    assert not (ROOT / "knowledge" / "analysis_context" / current_event["event_id"]).exists()
    stored = repo.run(result["run_id"])["repeat_results"][0]
    assert stored["candidate_event_id"] == historical_event["event_id"]
    confirmed = repo.confirm_repeat(stored["repeat_result_id"], "CONFIRMED_REPEAT", "tester", "机理一致")
    assert confirmed["review_status"] == "CONFIRMED_REPEAT"
    no_candidate_repo = MajorKnowledgeRepository(tmp / "empty.sqlite3", tmp / "empty-attachments")
    no_candidate_service = MajorCaseService(no_candidate_repo)
    solo = no_candidate_service.create_case("孤立案例", "G1")
    solo_event = no_candidate_repo.upsert_event(solo["case_id"], internal_event_key="internal")
    empty_adapter = LegacyRepeatAdapter(no_candidate_repo, ROOT, tmp / "empty-runs")
    empty = empty_adapter.run(solo_event["event_id"], mock=True)
    assert empty["decision"] == "INSUFFICIENT_EVIDENCE"


def test_legacy_event_views_preserve_different_mechanisms_for_human_review(env) -> None:
    tmp, _, repo, service = env
    case_a, event_a = _active_case(service, repo, tmp, "机理A", "ITR20260001", "mechanism-a")
    different = _docx(tmp / "mechanism-b.docx", [
        ("问题经过", ["通信中断，表象与历史问题相似。"]),
        ("根因分析", ["根因是连接器松动，不是状态机竞争。"]),
        ("整改措施", ["重新锁紧连接器。"]),
        ("验证结果", ["振动测试通过。"]),
    ])
    case_b = service.create_case("机理B", "G1")
    intake = service.ingest(case_b["case_id"], different, current_itrs=["ITR20260002"])
    service.extract(case_b["case_id"], intake["version_id"])
    for entry in repo.entries(case_b["case_id"]):
        if entry["status"] != "MISSING":
            service.review_entry(entry["entry_id"], content=entry["content"], action="CONFIRM", reviewer="tester")
    repo.update_case_status(case_b["case_id"], "ACTIVE")
    event_b = repo.events(case_b["case_id"])[0]
    adapter = LegacyRepeatAdapter(repo, ROOT, tmp / "mechanism-runs")
    view_a, view_b = adapter.export_event_view(event_a["event_id"]), adapter.export_event_view(event_b["event_id"])
    assert "状态机" in json.dumps(view_a["analysis"]["root_cause"], ensure_ascii=False)
    assert "连接器" in json.dumps(view_b["analysis"]["root_cause"], ensure_ascii=False)
    result = adapter.run(event_b["event_id"], mock=True)
    stored = repo.run(result["run_id"])["repeat_results"][0]
    assert stored["review_status"] == "PENDING"


def test_concurrent_run_directory_isolation(env) -> None:
    tmp, _, repo, service = env
    _, event1 = _active_case(service, repo, tmp, "案例A", "ITR20260001", "a")
    _, event2 = _active_case(service, repo, tmp, "案例B", "ITR20260002", "b")
    adapter = LegacyRepeatAdapter(repo, ROOT, tmp / "parallel-runs")
    results = []
    threads = [threading.Thread(target=lambda event=event: results.append(adapter.run(event["event_id"], mock=True))) for event in (event1, event2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 2
    assert len({item["run_directory"] for item in results}) == 2


def test_backup_restore_and_legacy_import_preview_idempotency(env) -> None:
    tmp, _, repo, service = env
    case = service.create_case("备份案例", "G1")
    service.ingest(case["case_id"], _full_review(tmp / "backup.docx"), current_itrs=["ITR20260001"])
    backup = create_backup(repo, tmp / "backup.zip")
    assert backup["attachments"] == 1
    restored = restore_backup(tmp / "backup.zip", tmp / "restored" / "knowledge.sqlite3", tmp / "restored" / "attachments")
    assert restored["schema_version"] == 1 and restored["attachment_count"] == 1
    restored_repo = MajorKnowledgeRepository(restored["database"], restored["attachments"])
    assert restored_repo.list_cases()["total"] == 1

    importer = LegacyCaseImporter(repo, ROOT / "schema/standard_case.schema.json")
    preview = importer.preview(ROOT / "tests/samples/valid_standard_case.json", "G1")
    assert preview["schema_valid"] is True and preview["state"] == "PREVIEW"
    repeated_preview = importer.preview(ROOT / "tests/samples/valid_standard_case.json", "G1")
    assert repeated_preview["reused"] is True
    imported = importer.confirm(preview["import_id"], reviewer="tester")
    repeated = importer.confirm(preview["import_id"], reviewer="tester")
    assert imported["case_id"] == repeated["case_id"] and repeated["reused"] is True


def test_list_query_plan_uses_indexes(env) -> None:
    _, _, repo, service = env
    for index in range(25):
        service.create_case(f"案例{index}", f"G{index % 2}")
    result = service.list_cases(group_code="G1", page=1, page_size=10)
    assert result["total"] == 12 and len(result["items"]) == 10
    with repo.connect() as connection:
        plan = " ".join(str(tuple(row)) for row in connection.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM kb_case WHERE group_code='G1' AND status='DRAFT' ORDER BY updated_at DESC LIMIT 20"
        ))
    assert "idx_kb_case_group_" in plan


def test_major_case_web_routes_use_independent_database(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from quality_knowledge.web.app import create_app

    business = tmp_path / "web-business.sqlite3"
    major_root = tmp_path / "web-major"
    monkeypatch.setenv("MAJOR_KNOWLEDGE_DATA_ROOT", str(major_root))
    app = create_app(business)
    client = TestClient(app)
    page = client.get("/knowledge/major-cases")
    assert page.status_code == 200 and "重大复盘知识库" in page.text
    created = client.post("/knowledge/major-cases", data={"title": "Web案例", "group_code": "G1", "domain": "软件"}, follow_redirects=False)
    assert created.status_code == 303
    assert (major_root / "knowledge.sqlite3").exists()
    with sqlite3.connect(business) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='kb_case'").fetchone()


def test_req022_package_contains_no_runtime_data(tmp_path: Path) -> None:
    from scripts.build_req022_package import build

    output = tmp_path / "req022.zip"
    result = build(output)
    assert result["contains_runtime_data"] is False
    with ZipFile(output) as archive:
        names = archive.namelist()
    assert any(name.endswith("REQ-022_MANIFEST.json") for name in names)
    assert not any(name.endswith((".db", ".sqlite", ".sqlite3", ".log")) for name in names)
    assert not any("/sources/" in name or "/knowledge/raw_" in name or "/output/" in name for name in names)
