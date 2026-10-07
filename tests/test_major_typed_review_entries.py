from __future__ import annotations

from pathlib import Path

import pytest

from quality_knowledge.major_cases.document_parser import ParseResult, ParsedFragment
from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from repositories import JsonArtifactRepository
from services.major_case_production import MajorCaseProductionService, MajorProductionError


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_TYPES = {
    "TRC_OCCURRENCE", "TRC_ESCAPE", "MRC_OCCURRENCE", "MRC_ESCAPE",
    "TECHNICAL_ACTION", "MANAGEMENT_ACTION", "CORRECTIVE_ACTION", "PREVENTIVE_ACTION",
}


def _setup(tmp_path: Path, *, formal_conflict: bool = False, provider=None):
    repository = MajorKnowledgeRepository(tmp_path / "major.db", tmp_path / "attachments")
    case = repository.create_case("Typed review test", "MAJOR")
    event = repository.upsert_event(case["case_id"], standard_itr="ITR20261005", title="Review event")
    fact = repository.add_source_fact_revision(
        case["case_id"], source_type="EXCEL", source_ref="batch.xlsx#row:2", raw={},
        normalized={
            "itr_id": "ITR20261005", "original_description": "控制器掉电后配置异常",
            "trc_occurrence": "板级保护缺失",
            "trc_escape": "测试未覆盖复位边界", "mrc_occurrence": "评审遗漏风险",
            "mrc_escape": "验收检查表缺项",
        }, actor="test",
    )
    excel_link = repository.add_source_link(
        case["case_id"], event["event_id"],
        {"record_id": fact["source_fact_revision_id"], "source_type": "MAJOR_EXCEL_SOURCE_FACT"},
        standard_itr="ITR20261005", role="CURRENT_EVENT",
        status="CONFLICT" if formal_conflict else "LINKED",
    )
    report_path = tmp_path / "report.pdf"
    report_path.write_bytes(b"test-only-placeholder")
    document = repository.ingest_file(case["case_id"], report_path)
    fragments = [
        ParsedFragment(index, "", "PAGE", f"page:{index}", "TEXT", f"{heading}\n{content}")
        for index, (heading, content) in enumerate([
            ("问题描述", "控制器掉电后配置异常，重启无法恢复"),
            ("TRC发生", "复位保护时序设计不足"),
            ("TRC流出", "复位边界未纳入回归测试"),
            ("MRC发生", "设计评审未识别异常窗口"),
            ("MRC流出", "验收清单未要求该项验证"),
            ("纠正措施", "修复当前版本的保护时序"),
            ("技术措施", "增加硬件看门狗保护"),
            ("技术措施", "增加掉电恢复状态校验"),
            ("验证结果", "完成连续掉电恢复测试，未再复现"),
        ], start=1)
    ]
    repository.save_parse_result(document["version_id"], ParseResult("PDF", fragments))
    pdf_link = repository.add_source_link(
        case["case_id"], event["event_id"],
        {"record_id": document["version_id"], "source_type": "MAJOR_DOCUMENT_VERSION"},
        standard_itr="ITR20261005", role="CURRENT_EVENT", status="LINKED",
    )
    service = MajorCaseProductionService(
        repository, JsonArtifactRepository(tmp_path / "artifacts"), tmp_path / "runtime.db",
        provider=provider, project_root=ROOT,
    )
    return repository, case, event, fact, document, excel_link, pdf_link, service


def _multi_event_source_fusion_service(tmp_path: Path, provider=None):
    repository = MajorKnowledgeRepository(tmp_path / "major.db", tmp_path / "attachments")
    case = repository.create_case("Multi Event typed review", "MAJOR")
    events = [
        repository.upsert_event(
            case["case_id"], standard_itr=f"ITR2026101{index}",
            title=f"Event {index}",
        )
        for index in (1, 2)
    ]
    facts = []
    links = []
    for index, event in enumerate(events, start=1):
        fact = repository.add_source_fact_revision(
            case["case_id"], source_type="EXCEL", source_ref=f"batch.xlsx#row:{index}",
            raw={}, normalized={
                "itr_id": event["standard_itr"],
                "original_description": f"Event {index} symptom",
                "trc_occurrence": f"Event {index} TRC occurrence",
                "trc_escape": f"Event {index} TRC escape",
                "mrc_occurrence": f"Event {index} MRC occurrence",
                "mrc_escape": f"Event {index} MRC escape",
            }, actor="test",
        )
        link = repository.add_source_link(
            case["case_id"], event["event_id"],
            {"record_id": fact["source_fact_revision_id"], "source_type": "MAJOR_EXCEL_SOURCE_FACT"},
            standard_itr=event["standard_itr"], role="CURRENT_EVENT", status="LINKED",
        )
        facts.append(fact)
        links.append(link)
    service = MajorCaseProductionService(
        repository, JsonArtifactRepository(tmp_path / "artifacts"), tmp_path / "runtime.db",
        provider=provider, project_root=ROOT,
    )
    return repository, case, events, facts, links, service


def test_source_fusion_creates_typed_candidates_with_exact_action_evidence_and_safe_missing(tmp_path: Path):
    repository, case, event, _, document, _, pdf_link, service = _setup(tmp_path)
    with repository.connect() as connection:
        tables_before = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    result = service.analyze(case["case_id"])
    candidates = result["candidates"]

    assert result["mode"] == "SOURCE_FUSION"
    candidate_types = {item["entry_type"] for item in candidates}
    assert CANONICAL_TYPES <= candidate_types
    assert {"ISSUE_FACT", "VERIFICATION"} <= candidate_types
    assert not {item["entry_type"] for item in candidates} & {"ROOT_CAUSE", "ACTION"}
    assert all(item["origin"] == "SOURCE_FUSION" for item in candidates)
    technical = [item for item in candidates if item["entry_type"] == "TECHNICAL_ACTION"]
    assert [item["content"] for item in technical] == ["增加硬件看门狗保护", "增加掉电恢复状态校验"]
    fragments = repository.fragments(document["version_id"])
    assert [item["evidence"][0]["fragment_id"] for item in technical] == [
        fragments[6]["fragment_id"], fragments[7]["fragment_id"],
    ]
    assert all(item["evidence"][0]["source_link_id"] == pdf_link["source_link_id"] for item in technical)
    missing = [item for item in candidates if item["status"] == "MISSING"]
    assert missing
    assert all(item["content"] == "" and item["evidence"] == [] for item in missing)
    issue_fact = next(item for item in candidates if item["entry_type"] == "ISSUE_FACT")
    verification = next(item for item in candidates if item["entry_type"] == "VERIFICATION")
    assert issue_fact["analysis_metadata"]["source_status"] == "MULTI_SOURCE"
    assert verification["content"] == "完成连续掉电恢复测试，未再复现"
    assert verification["evidence"][0]["fragment_id"] == fragments[-1]["fragment_id"]
    assert repository.schema_version() == 3
    with repository.connect() as connection:
        tables_after = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert tables_after == tables_before


def test_multi_source_requires_human_decision_and_corrected_revision_copies_evidence(tmp_path: Path):
    repository, case, _, _, _, excel_link, _, service = _setup(tmp_path)
    result = service.analyze(case["case_id"])
    entry = next(item for item in result["candidates"] if item["entry_type"] == "TRC_OCCURRENCE")
    assert entry["status"] == "PENDING"
    assert entry["analysis_metadata"]["source_status"] == "MULTI_SOURCE"
    assert {item["source_type"] for item in entry["analysis_metadata"]["source_values"]} == {"EXCEL", "PDF"}
    assert {item["source_link_id"] for item in entry["evidence"]} >= {excel_link["source_link_id"]}

    with pytest.raises(MajorProductionError, match="MAJOR_SEMANTIC_REVIEW_DECISION_REQUIRED"):
        service.confirm_entry(entry["entry_id"], reviewer="reviewer")
    corrected = service.confirm_entry(
        entry["entry_id"], reviewer="reviewer", content="人工确认：保护时序不足，测试覆盖缺失",
        reason="Review 原始记录和 PDF 细节后合并判断", action="CORRECT",
    )
    assert corrected["status"] == "CORRECTED"
    assert corrected["assertion_kind"] == "HUMAN_REVISION"
    assert corrected["origin"] == "HUMAN"
    evidence_identity = lambda items: [
        (item["fragment_id"], item["source_link_id"], item["locator"], item["excerpt"])
        for item in items
    ]
    assert sorted(evidence_identity(corrected["evidence"]), key=str) == sorted(evidence_identity(entry["evidence"]), key=str)
    with pytest.raises(MajorProductionError, match="MAJOR_CONFIRMATION_REQUIRES_PENDING_AI_CANDIDATE"):
        service.confirm_entry(entry["entry_id"], reviewer="reviewer", content="overwritten")


def test_source_candidates_can_be_confirmed_and_confirmed_revision_survives_reanalysis(tmp_path: Path):
    repository, case, _, _, _, _, _, service = _setup(tmp_path)
    first = service.analyze(case["case_id"])
    candidate = next(item for item in first["candidates"] if item["entry_type"] == "TRC_OCCURRENCE")
    confirmed = service.confirm_entry(
        candidate["entry_id"], reviewer="reviewer", content=candidate["content"], reason="checked",
    )
    second = service.analyze(case["case_id"])

    assert confirmed["status"] == "CONFIRMED"
    assert confirmed["assertion_kind"] == "HUMAN_REVISION"
    assert confirmed["origin"] == "HUMAN"
    assert repository.entry(candidate["entry_id"])["archived_at"] is None
    assert repository.entry(candidate["entry_id"])["current_revision_id"] == confirmed["current_revision_id"]
    assert all(item["entry_id"] != candidate["entry_id"] for item in second["candidates"])
    with repository.connect() as connection:
        old_pending = connection.execute(
            "SELECT archived_at FROM kb_entry WHERE entry_id=?", (next(
                item["entry_id"] for item in first["candidates"] if item["entry_type"] == "MRC_ESCAPE"
            ),)
        ).fetchone()
        assert old_pending["archived_at"] is not None


def test_corrected_human_revision_survives_reanalysis_and_standardization(tmp_path: Path):
    provider_calls = []

    def provider(provider_input, pending_specs, _context):
        provider_calls.append({
            "input": provider_input,
            "object_ids": [spec["object_id"] for spec in pending_specs],
        })
        source_content = {
            item["entry_id"]: item["source_content"]
            for item in provider_input["entries"]
        }
        return [
            {
                "object_id": spec["object_id"],
                "data": {"content": "标准化建议：" + source_content[spec["object_id"]]},
            }
            for spec in pending_specs
        ]

    repository, case, service = _excel_only_standardization_service(tmp_path, provider)
    first = service.analyze(case["case_id"])
    candidate = next(
        item for item in first["candidates"] if item["entry_type"] == "TRC_OCCURRENCE"
    )
    corrected = service.confirm_entry(
        candidate["entry_id"],
        reviewer="reviewer",
        content="人工修订：增加复位保护时序检查",
        reason="结合原始 Source Fact 核正表述",
        action="CORRECT",
    )

    assert corrected["status"] == "CORRECTED"
    assert corrected["origin"] == "HUMAN"
    assert corrected["assertion_kind"] == "HUMAN_REVISION"
    expected_entry_id = corrected["entry_id"]
    expected_revision_id = corrected["current_revision_id"]
    expected_content = corrected["content"]
    expected_evidence = sorted(
        (
            item["evidence_id"], item["fragment_id"], item["source_link_id"],
            item["locator"], item["excerpt"],
        )
        for item in corrected["evidence"]
    )

    second = service.analyze(case["case_id"])
    after = repository.entry(expected_entry_id)

    assert second["standardization"]["status"] == "COMPLETED"
    assert after["entry_id"] == expected_entry_id
    assert after["current_revision_id"] == expected_revision_id
    assert after["content"] == expected_content
    assert sorted(
        (
            item["evidence_id"], item["fragment_id"], item["source_link_id"],
            item["locator"], item["excerpt"],
        )
        for item in after["evidence"]
    ) == expected_evidence
    assert after["archived_at"] is None
    assert after["status"] == "CORRECTED"
    assert after["origin"] == "HUMAN"
    assert after["assertion_kind"] == "HUMAN_REVISION"
    assert all(
        expected_entry_id not in call["object_ids"]
        for call in provider_calls[1:]
    )
    assert all(item["entry_id"] != expected_entry_id for item in second["candidates"])


def test_source_fusion_archives_old_pending_ai_but_not_confirmed_human_entries(tmp_path: Path):
    repository, case, event, _, _, _, _, service = _setup(tmp_path)
    stale = repository.add_entry(
        case["case_id"], "ROOT_CAUSE", "stale generic cause", assertion_kind="AI_INFERENCE",
        origin="AI", status="PENDING", event_id=event["event_id"],
    )
    human = repository.add_entry(
        case["case_id"], "ACTION", "confirmed historical action", assertion_kind="HUMAN_REVISION",
        origin="HUMAN", status="CONFIRMED", event_id=event["event_id"],
    )

    service.analyze(case["case_id"])

    assert repository.entry(stale["entry_id"])["archived_at"] is not None
    assert repository.entry(human["entry_id"])["archived_at"] is None


def test_formal_source_relation_conflict_is_review_required_and_no_side_is_chosen(tmp_path: Path):
    _, case, _, _, _, _, _, service = _setup(tmp_path, formal_conflict=True)
    result = service.analyze(case["case_id"])
    entry = next(item for item in result["candidates"] if item["entry_type"] == "TRC_OCCURRENCE")
    assert entry["analysis_metadata"]["source_status"] == "CONFLICT"
    assert entry["analysis_metadata"]["review_status"] == "REVIEW_REQUIRED"
    assert "[EXCEL] 板级保护缺失" in entry["content"]
    assert "[PDF] 复位保护时序设计不足" in entry["content"]


def test_formal_document_relation_conflict_keeps_verification_human_reviewable(tmp_path: Path):
    repository, case, event, _, document, _, _, service = _setup(tmp_path)
    repository.add_source_link(
        case["case_id"], event["event_id"],
        {"record_id": document["version_id"], "source_type": "MAJOR_DOCUMENT_VERSION"},
        standard_itr="ITR20261005", role="CURRENT_EVENT", status="CONFLICT",
    )

    result = service.analyze(case["case_id"])
    verification = next(item for item in result["candidates"] if item["entry_type"] == "VERIFICATION")

    assert verification["analysis_metadata"]["source_status"] == "CONFLICT"
    assert verification["analysis_metadata"]["review_status"] == "REVIEW_REQUIRED"
    assert verification["content"] == "完成连续掉电恢复测试，未再复现"


def _excel_only_standardization_service(tmp_path: Path, provider):
    repository = MajorKnowledgeRepository(tmp_path / "major.db", tmp_path / "attachments")
    case = repository.create_case("Runtime standardization", "MAJOR")
    event = repository.upsert_event(case["case_id"], standard_itr="ITR20261006", title="Runtime event")
    fact = repository.add_source_fact_revision(
        case["case_id"], source_type="EXCEL", source_ref="batch.xlsx#row:3", raw={},
        normalized={
            "itr_id": "ITR20261006", "original_description": "设备异常重启",
            "trc_occurrence": "板级保护缺失", "trc_escape": "测试未覆盖复位边界",
            "mrc_occurrence": "评审遗漏风险", "mrc_escape": "验收检查表缺项",
        }, actor="test",
    )
    repository.add_source_link(
        case["case_id"], event["event_id"],
        {"record_id": fact["source_fact_revision_id"], "source_type": "MAJOR_EXCEL_SOURCE_FACT"},
        standard_itr="ITR20261006", role="CURRENT_EVENT", status="LINKED",
    )
    service = MajorCaseProductionService(
        repository, JsonArtifactRepository(tmp_path / "artifacts"), tmp_path / "runtime.db",
        provider=provider, project_root=ROOT,
    )
    return repository, case, service


def _add_pdf_semantic_fragment(
    repository: MajorKnowledgeRepository,
    case_id: str,
    event_id: str,
    tmp_path: Path,
    *,
    name: str,
    heading: str,
    content: str,
):
    report_path = tmp_path / f"{name}.pdf"
    report_path.write_bytes(b"test-only-placeholder")
    document = repository.ingest_file(case_id, report_path)
    repository.save_parse_result(
        document["version_id"],
        ParseResult("PDF", [
            ParsedFragment(1, "", "PAGE", "page:1", "TEXT", f"{heading}\n{content}")
        ]),
    )
    repository.add_source_link(
        case_id,
        event_id,
        {"record_id": document["version_id"], "source_type": "MAJOR_DOCUMENT_VERSION"},
        standard_itr="ITR20261006",
        role="CURRENT_EVENT",
        status="LINKED",
    )


def _mixed_source_standardization_service(tmp_path: Path, provider):
    repository, case, service = _excel_only_standardization_service(tmp_path, provider)
    event = repository.events(case["case_id"])[0]
    _add_pdf_semantic_fragment(
        repository,
        case["case_id"],
        event["event_id"],
        tmp_path,
        name="multisource-report",
        heading="TRC发生",
        content="复位保护时序设计不足",
    )
    return repository, case, service


def test_unified_runtime_adds_only_evidence_bound_standardization_suggestions(tmp_path: Path):
    observed = {}

    def provider(provider_input, pending_specs, _context):
        observed["input"] = provider_input
        observed["specs"] = pending_specs
        return [
            {
                "object_id": spec["object_id"],
                "data": {"content": "标准化：" + next(
                    item["source_content"] for item in provider_input["entries"]
                    if item["entry_id"] == spec["object_id"]
                )},
            }
            for spec in pending_specs
        ]

    repository, case, service = _excel_only_standardization_service(tmp_path, provider)
    result = service.analyze(case["case_id"])

    assert result["standardization"]["status"] == "COMPLETED"
    assert len(observed["specs"]) == 4
    assert {spec["locator"]["entry_type"] for spec in observed["specs"]} == {
        "TRC_OCCURRENCE", "TRC_ESCAPE", "MRC_OCCURRENCE", "MRC_ESCAPE",
    }
    assert observed["input"]["rules"]["output_is_suggestion_only"] is True
    for entry_type in CANONICAL_TYPES & {"TRC_OCCURRENCE", "TRC_ESCAPE", "MRC_OCCURRENCE", "MRC_ESCAPE"}:
        item = next(candidate for candidate in result["candidates"] if candidate["entry_type"] == entry_type)
        assert item["content"] != item["analysis_metadata"]["standardization"]["content"]
        assert item["analysis_metadata"]["standardization"]["status"] == "PROPOSED"
        assert item["analysis_metadata"]["standardization"]["evidence_ids"] == [
            evidence["evidence_id"] for evidence in item["evidence"]
        ]
        assert repository.entry(item["entry_id"])["content"] == item["content"]
    assert next(item for item in result["candidates"] if item["entry_type"] == "VERIFICATION")["status"] == "MISSING"


def test_unified_runtime_failure_leaves_source_candidates_reviewable(tmp_path: Path):
    def failing_provider(_provider_input, _pending_specs, _context):
        raise RuntimeError("simulated provider failure")

    repository, case, service = _excel_only_standardization_service(tmp_path, failing_provider)
    result = service.analyze(case["case_id"])

    assert result["standardization"]["status"] == "FAILED"
    assert result["candidates"]
    assert all(item["origin"] == "SOURCE_FUSION" for item in result["candidates"])
    assert all(item["status"] in {"PENDING", "MISSING"} for item in result["candidates"])
    assert repository.entries(case["case_id"])


def _assert_failed_standardization_preserves_source_candidates(repository, result, case_id):
    candidates = result["candidates"]
    assert candidates
    assert not {item["entry_type"] for item in candidates} & {"ROOT_CAUSE", "ACTION"}

    for candidate in candidates:
        saved = repository.entry(candidate["entry_id"])
        assert saved["content"] == candidate["content"]
        assert saved["status"] == candidate["status"]
        assert saved["origin"] == "SOURCE_FUSION"
        assert saved["analysis_metadata"].get("standardization") is None
        assert sorted(
            (
                item["evidence_id"], item["fragment_id"], item["source_link_id"],
                item["locator"], item["excerpt"],
            )
            for item in saved["evidence"]
        ) == sorted(
            (
                item["evidence_id"], item["fragment_id"], item["source_link_id"],
                item["locator"], item["excerpt"],
            )
            for item in candidate["evidence"]
        )

    missing = [item for item in candidates if item["status"] == "MISSING"]
    assert missing
    assert all(item["content"] == "" and item["evidence"] == [] for item in missing)
    assert all(item["analysis_metadata"].get("standardization") is None for item in missing)
    ambiguous = [
        item for item in candidates
        if item["analysis_metadata"].get("source_status") in {"MULTI_SOURCE", "CONFLICT"}
    ]
    assert "MULTI_SOURCE" in {item["analysis_metadata"]["source_status"] for item in ambiguous}
    assert all(item["analysis_metadata"]["review_status"] == "REVIEW_REQUIRED" for item in ambiguous)
    assert all(item["analysis_metadata"].get("standardization") is None for item in ambiguous)
    assert repository.entries(case_id)


def test_provider_not_configured_keeps_source_candidates_reviewable(tmp_path: Path):
    repository, case, service = _mixed_source_standardization_service(tmp_path, None)

    result = service.analyze(case["case_id"])

    assert result["standardization"]["status"] == "SKIPPED_PROVIDER_NOT_CONFIGURED"
    _assert_failed_standardization_preserves_source_candidates(repository, result, case["case_id"])


@pytest.mark.parametrize(
    "failure_mode",
    ["timeout", "exception", "invalid_json", "schema_invalid", "incomplete", "runtime_partial"],
)
def test_provider_failure_matrix_preserves_source_candidates(tmp_path: Path, failure_mode: str):
    calls = []

    def provider(provider_input, pending_specs, _context):
        calls.append((provider_input, pending_specs))
        first_id = pending_specs[0]["object_id"]
        valid = {"object_id": first_id, "data": {"content": "不得成为权威内容"}}
        if failure_mode == "timeout":
            raise TimeoutError("simulated provider timeout")
        if failure_mode == "exception":
            raise RuntimeError("simulated provider exception")
        if failure_mode == "invalid_json":
            return ["{not valid JSON"]
        if failure_mode == "schema_invalid":
            return [{**valid, "schema_valid": False}]
        if failure_mode == "incomplete":
            return [{**valid, "complete_object": False, "finish_reason": "length"}]
        if failure_mode == "runtime_partial":
            return [valid]
        raise AssertionError(f"unknown failure mode: {failure_mode}")

    repository, case, service = _mixed_source_standardization_service(tmp_path, provider)
    result = service.analyze(case["case_id"])

    assert result["standardization"]["status"] == "FAILED"
    assert calls
    _assert_failed_standardization_preserves_source_candidates(repository, result, case["case_id"])

    provider_entries = {
        item["entry_id"]: item for item in calls[0][0]["entries"]
    }
    blocked_ids = {
        item["entry_id"] for item in result["candidates"]
        if item["status"] == "MISSING"
        or item["analysis_metadata"].get("source_status") in {"MULTI_SOURCE", "CONFLICT"}
    }
    assert not blocked_ids & set(provider_entries)
    for entry_id, source_entry in provider_entries.items():
        candidate = next(item for item in result["candidates"] if item["entry_id"] == entry_id)
        assert candidate["content"] == source_entry["source_content"]
        assert sorted(
            (
                item["evidence_id"], item["fragment_id"], item["source_link_id"],
                item["locator"], item["excerpt"],
            )
            for item in candidate["evidence"]
        ) == sorted(
            (
                item["evidence_id"], item["fragment_id"], item["source_link_id"],
                item["locator"], item["excerpt"],
            )
            for item in source_entry["evidence"]
        )


def test_multi_source_and_conflict_candidates_are_never_sent_to_runtime(tmp_path: Path):
    calls = []

    def provider(*args):
        calls.append(args)
        return []

    _, case, _, _, _, _, _, service = _setup(tmp_path, provider=provider)
    result = service.analyze(case["case_id"])
    assert result["standardization"]["status"] == "SKIPPED_NO_UNAMBIGUOUS_EVIDENCE"
    assert calls == []
    multi_source = [
        item for item in result["candidates"]
        if item["analysis_metadata"].get("source_status") == "MULTI_SOURCE"
    ]
    assert multi_source
    assert all(item["analysis_metadata"]["review_status"] == "REVIEW_REQUIRED" for item in multi_source)
    assert all(item["analysis_metadata"].get("standardization") is None for item in multi_source)

    conflict_dir = tmp_path / "conflict"
    conflict_dir.mkdir()
    _, conflict_case, _, _, _, _, _, conflict_service = _setup(
        conflict_dir, formal_conflict=True, provider=provider,
    )
    conflict = conflict_service.analyze(conflict_case["case_id"])
    assert conflict["standardization"]["status"] == "SKIPPED_NO_UNAMBIGUOUS_EVIDENCE"
    assert calls == []
    conflict_candidates = [
        item for item in conflict["candidates"]
        if item["analysis_metadata"].get("source_status") == "CONFLICT"
    ]
    assert conflict_candidates
    assert all(item["analysis_metadata"]["review_status"] == "REVIEW_REQUIRED" for item in conflict_candidates)
    assert all(item["analysis_metadata"].get("standardization") is None for item in conflict_candidates)


def test_confirming_edited_available_candidate_requires_explicit_correct_action(tmp_path: Path):
    _, case, _, _, _, _, _, service = _setup(tmp_path)
    candidate = next(
        item for item in service.analyze(case["case_id"])["candidates"]
        if item["entry_type"] == "TECHNICAL_ACTION"
    )
    with pytest.raises(MajorProductionError, match="MAJOR_EDIT_REQUIRES_CORRECT_ACTION"):
        service.confirm_entry(
            candidate["entry_id"], reviewer="reviewer", content="rewritten", action="CONFIRM",
        )
    with pytest.raises(MajorProductionError, match="MAJOR_SEMANTIC_CORRECTION_REASON_REQUIRED"):
        service.confirm_entry(
            candidate["entry_id"], reviewer="reviewer", content="rewritten", action="CORRECT",
        )
    corrected = service.confirm_entry(
        candidate["entry_id"], reviewer="reviewer", content="rewritten", reason="clearer", action="CORRECT",
    )
    assert corrected["status"] == "CORRECTED"


def test_multi_event_analysis_requires_explicit_valid_event_selection(tmp_path: Path):
    _, case, events, _, _, service = _multi_event_source_fusion_service(tmp_path)

    with pytest.raises(MajorProductionError, match="MAJOR_ANALYSIS_EVENT_SELECTION_REQUIRED"):
        service.analyze(case["case_id"])
    with pytest.raises(MajorProductionError, match="MAJOR_ANALYSIS_EVENT_INVALID"):
        service.analyze(case["case_id"], event_id="not-an-event-in-this-case")

    selected = service.analyze(case["case_id"], event_id=events[1]["event_id"])
    assert selected["event_id"] == events[1]["event_id"]
    assert selected["candidates"]
    assert all(item["event_id"] == events[1]["event_id"] for item in selected["candidates"])
    with pytest.raises(MajorProductionError, match="MAJOR_REVIEW_EVENT_SELECTION_REQUIRED"):
        service.confirm_entry(selected["candidates"][0]["entry_id"], reviewer="reviewer")


def test_multi_event_source_fact_binding_and_reanalysis_are_event_scoped(tmp_path: Path):
    def provider(provider_input, pending_specs, _context):
        return [
            {
                "object_id": spec["object_id"],
                "data": {"content": "Suggestion: " + next(
                    item["source_content"] for item in provider_input["entries"]
                    if item["entry_id"] == spec["object_id"]
                )},
            }
            for spec in pending_specs
        ]

    repository, case, events, facts, links, service = _multi_event_source_fusion_service(
        tmp_path, provider,
    )
    event_a, event_b = events
    fact_a, fact_b = facts
    link_a, link_b = links

    analysis_a = service.analyze(case["case_id"], event_id=event_a["event_id"])
    entries_a = analysis_a["candidates"]
    assert analysis_a["event_id"] == event_a["event_id"]
    assert all(item["event_id"] == event_a["event_id"] for item in entries_a)
    occurrence_a = next(item for item in entries_a if item["entry_type"] == "TRC_OCCURRENCE")
    assert occurrence_a["content"] == "Event 1 TRC occurrence"
    assert occurrence_a["analysis_metadata"]["source_fact_revision_id"] == fact_a["source_fact_revision_id"]
    assert {item["source_link_id"] for item in occurrence_a["evidence"]} == {link_a["source_link_id"]}
    assert link_b["source_link_id"] not in {
        item["source_link_id"] for entry in entries_a for item in entry["evidence"]
    }

    corrected = service.confirm_entry(
        occurrence_a["entry_id"], reviewer="reviewer", event_id=event_a["event_id"],
        content="Human corrected Event 1 occurrence", reason="Event 1 source reviewed", action="CORRECT",
    )
    with pytest.raises(MajorProductionError, match="MAJOR_REVIEW_EVENT_MISMATCH"):
        service.confirm_entry(
            occurrence_a["entry_id"], reviewer="reviewer", event_id=event_b["event_id"],
            content="Cross-event overwrite", reason="wrong event", action="CORRECT",
        )

    analysis_b = service.analyze(case["case_id"], event_id=event_b["event_id"])
    entries_b = analysis_b["candidates"]
    assert analysis_b["event_id"] == event_b["event_id"]
    assert all(item["event_id"] == event_b["event_id"] for item in entries_b)
    occurrence_b = next(item for item in entries_b if item["entry_type"] == "TRC_OCCURRENCE")
    assert occurrence_b["content"] == "Event 2 TRC occurrence"
    assert occurrence_b["analysis_metadata"]["source_fact_revision_id"] == fact_b["source_fact_revision_id"]
    assert {item["source_link_id"] for item in occurrence_b["evidence"]} == {link_b["source_link_id"]}

    snapshot_b = {
        item["entry_id"]: (
            item["current_revision_id"], item["content"],
            tuple(sorted(evidence["evidence_id"] for evidence in item["evidence"])),
            item["archived_at"],
        )
        for item in entries_b
    }
    service.analyze(case["case_id"], event_id=event_a["event_id"])

    after_corrected = repository.entry(corrected["entry_id"])
    assert after_corrected["status"] == "CORRECTED"
    assert after_corrected["origin"] == "HUMAN"
    assert after_corrected["assertion_kind"] == "HUMAN_REVISION"
    assert after_corrected["current_revision_id"] == corrected["current_revision_id"]
    assert after_corrected["content"] == corrected["content"]
    assert after_corrected["archived_at"] is None
    for entry_id, expected in snapshot_b.items():
        after = repository.entry(entry_id)
        assert after["event_id"] == event_b["event_id"]
        assert (
            after["current_revision_id"], after["content"],
            tuple(sorted(evidence["evidence_id"] for evidence in after["evidence"])),
            after["archived_at"],
        ) == expected
