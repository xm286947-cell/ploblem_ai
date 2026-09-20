from __future__ import annotations

import pytest

from quality_knowledge.major_cases.runtime_integration import (
    D01_AGENT_ID,
    D01_WORKFLOW_ID,
    MajorCaseRuntimeDomainAdapter,
    REPEAT_CASE_AGENT_ID,
)


class FakeRepository:
    def __init__(self):
        self.case = {"case_id": "CASE-1", "group_code": "G1"}
        self.version_row = {
            "version_id": "VER-1",
            "document_id": "DOC-1",
            "group_code": "G1",
            "version_no": 2,
            "content_hash": "abc123",
            "parser_version": "parser-v1",
            "media_type": "PDF",
        }
        self.event_row = {
            "event_id": "EVENT-1",
            "case_id": "CASE-1",
            "standard_itr": "ITR001",
        }
        self.fragment_rows = [
            {
                "fragment_id": "F1",
                "ordinal": 1,
                "section_path": "根因",
                "location_type": "PAGE",
                "location_ref": "p.3",
                "fragment_type": "TEXT",
                "text_content": "根因片段一",
                "text_hash": "h1",
            },
            {
                "fragment_id": "F2",
                "ordinal": 2,
                "section_path": "根因",
                "location_type": "PAGE",
                "location_ref": "p.4",
                "fragment_type": "TEXT",
                "text_content": "根因片段二",
                "text_hash": "h2",
            },
            {
                "fragment_id": "F3",
                "ordinal": 3,
                "section_path": "措施",
                "location_type": "PAGE",
                "location_ref": "p.6",
                "fragment_type": "TEXT",
                "text_content": "措施片段",
                "text_hash": "h3",
            },
        ]
        self.entry_rows = []
        self.unscoped_rows = []

    def get_case(self, case_id):
        return self.case if case_id == self.case["case_id"] else None

    def version(self, version_id):
        return self.version_row if version_id == self.version_row["version_id"] else None

    def event(self, event_id):
        return self.event_row if event_id == self.event_row["event_id"] else None

    def fragments(self, version_id):
        assert version_id == self.version_row["version_id"]
        return list(self.fragment_rows)

    def entries(self, case_id):
        assert case_id == self.case["case_id"]
        return list(self.entry_rows)

    def unscoped_confirmed_entries(self, case_id):
        assert case_id == self.case["case_id"]
        return list(self.unscoped_rows)


def test_contract_freezes_canonical_ids_and_runtime_retry_ownership():
    contract = MajorCaseRuntimeDomainAdapter.contract_snapshot()

    assert D01_AGENT_ID == "major_issue.d01.extract"
    assert D01_WORKFLOW_ID == "major_issue_d01_v1"
    assert REPEAT_CASE_AGENT_ID == "major_issue.repeat_case"
    assert contract["retry_owner"] == "Unified Agent Runtime"
    assert contract["provider_boundary"] == {
        "actual_requests_per_attempt": 1,
        "adapter_retry": 0,
        "sdk_retry": 0,
        "analyzer_retry": 0,
    }
    assert contract["partition"] == "event_id"


def test_request_id_is_stable_and_changes_with_source_identity():
    first = MajorCaseRuntimeDomainAdapter.request_id(
        case_id="CASE-1",
        version_id="VER-1",
        event_id="EVENT-1",
        source_fingerprint="fp-a",
        skill_version_id="skill-v1",
    )
    same = MajorCaseRuntimeDomainAdapter.request_id(
        case_id="CASE-1",
        version_id="VER-1",
        event_id="EVENT-1",
        source_fingerprint="fp-a",
        skill_version_id="skill-v1",
    )
    changed = MajorCaseRuntimeDomainAdapter.request_id(
        case_id="CASE-1",
        version_id="VER-1",
        event_id="EVENT-1",
        source_fingerprint="fp-b",
        skill_version_id="skill-v1",
    )

    assert first == same
    assert first != changed
    assert first.startswith("major-d01:CASE-1:VER-1:EVENT-1:")


def test_source_bundle_preserves_event_partition_and_fragment_evidence():
    adapter = MajorCaseRuntimeDomainAdapter(FakeRepository())

    bundle = adapter.build_source_bundle("CASE-1", "VER-1", "EVENT-1")

    assert bundle.default_partition_key == "EVENT-1"
    assert len(bundle.sources) == 1
    assert bundle.sources[0].source.content_hash == "abc123"
    assert all(unit.partition_key == "EVENT-1" for unit in bundle.logical_units)
    assert [unit.unit_id for unit in bundle.logical_units] == ["F1", "F2", "F3"]
    assert bundle.logical_units[0].locator["location_ref"] == "p.3"
    assert bundle.logical_units[0].inline_payload["text_content"] == "根因片段一"

    assert len(bundle.atomic_groups) == 1
    assert bundle.atomic_groups[0].unit_ids == ["F1", "F2"]
    assert bundle.atomic_groups[0].grouping_hint == "根因"


def test_source_bundle_rejects_cross_case_event_scope():
    repository = FakeRepository()
    repository.event_row = {
        "event_id": "EVENT-1",
        "case_id": "CASE-OTHER",
        "standard_itr": "ITR999",
    }

    with pytest.raises(ValueError, match="EVENT_CASE_SCOPE_MISMATCH"):
        MajorCaseRuntimeDomainAdapter(repository).build_source_bundle(
            "CASE-1",
            "VER-1",
            "EVENT-1",
        )


def test_expected_objects_match_major_review_business_schema():
    objects = MajorCaseRuntimeDomainAdapter.expected_objects()

    assert [item.object_id for item in objects] == [
        "ISSUE_FACT",
        "ROOT_CAUSE",
        "ACTION",
        "VERIFICATION",
    ]
    assert [item.unit_id for item in objects] == [
        "entry:ISSUE_FACT",
        "entry:ROOT_CAUSE",
        "entry:ACTION",
        "entry:VERIFICATION",
    ]


def test_business_gate_requires_review_and_event_scope_completion():
    repository = FakeRepository()
    adapter = MajorCaseRuntimeDomainAdapter(repository)

    repository.entry_rows = [
        {"entry_id": "E1", "status": "PENDING"},
        {"entry_id": "E2", "status": "CONFIRMED"},
    ]
    first = adapter.business_gate("CASE-1")
    assert first["passed"] is False
    assert "HUMAN_REVIEW_INCOMPLETE" in first["reasons"]

    repository.entry_rows = [
        {"entry_id": "E1", "status": "CONFIRMED"},
        {"entry_id": "E2", "status": "CORRECTED"},
    ]
    repository.unscoped_rows = [
        {"entry_id": "E2", "status": "CORRECTED"},
    ]
    second = adapter.business_gate("CASE-1")
    assert second["passed"] is False
    assert "UNSCOPED_EVENT_KNOWLEDGE" in second["reasons"]

    repository.unscoped_rows = []
    third = adapter.business_gate("CASE-1")
    assert third == {
        "passed": True,
        "business_consumable": True,
        "reasons": [],
        "pending_entry_ids": [],
        "unscoped_entry_ids": [],
    }
