from __future__ import annotations

import pytest

from runtime import (
    EvidenceLocator,
    EvidenceReference,
    LongContentPolicy,
    RuntimeStatus,
    SourceRef,
    SqliteTaskStore,
)
from runtime.content import (
    AtomicUnitTooLargeError,
    SourceIdentityChangedError,
)
from runtime.engine import LightweightExecutionEngine
from runtime.adapters import (
    StorageCompatibilityAdapter,
    StorageFieldResult,
    StorageGoldenDiffError,
    StorageGoldenFieldComparator,
)


def datasheet(*, fingerprint="fp-v1", revision="1"):
    return SourceRef(
        source_id="datasheet-1",
        source_type="DATASHEET",
        revision=revision,
        content_hash=f"hash-{fingerprint}",
        fingerprint=fingerprint,
        uri="file://storage/device/datasheet.pdf",
    )


def field_evidence(src=None, *, page=12):
    src = src or datasheet()
    return EvidenceReference(
        evidence_id=f"ev-{src.fingerprint}-{page}",
        source=src,
        locator=EvidenceLocator(
            type="PAGE",
            value={
                "page": page,
                "table": "Endurance",
                "row": "P/E Cycle",
            },
        ),
        excerpt="P/E Cycle 3000 cycles at 25C",
    )


def test_st01_storage_linked_fields_keep_together(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    adapter = StorageCompatibilityAdapter(
        runtime,
        lambda payload, _context: payload,
    )

    bundle, plan = adapter.project_and_plan(
        source=datasheet(),
        fields=[
            {
                "field_id": "pe_cycle",
                "value": 3000,
                "unit": "cycles",
                "qualifier": "25C",
                "footnote": "typical endurance",
            },
            {
                "field_id": "capacity",
                "value": 64,
                "unit": "GB",
            },
        ],
        policy=LongContentPolicy(
            max_units_per_chunk=4,
            max_payload_chars=1000,
        ),
        partition_key="SSD-A",
    )

    group = next(
        item
        for item in bundle.atomic_groups
        if item.group_id == "linked-fields:pe_cycle"
    )
    assert group.policy.value == "KEEP_TOGETHER"
    assert group.unit_ids == [
        "pe_cycle:value",
        "pe_cycle:unit",
        "pe_cycle:qualifier",
        "pe_cycle:footnote",
    ]

    chunks_with_pe = [
        chunk
        for chunk in plan.chunks
        if any(uid.startswith("pe_cycle:") for uid in chunk.unit_ids)
    ]
    assert len(chunks_with_pe) == 1
    assert chunks_with_pe[0].unit_ids == group.unit_ids

    with pytest.raises(AtomicUnitTooLargeError):
        adapter.project_and_plan(
            source=datasheet(),
            fields=[
                {
                    "field_id": "pe_cycle",
                    "value": 3000,
                    "unit": "cycles",
                    "qualifier": "25C",
                    "footnote": "typical endurance",
                }
            ],
            policy=LongContentPolicy(
                max_units_per_chunk=3,
                max_payload_chars=1000,
            ),
            partition_key="SSD-A",
        )


def test_st02_storage_source_identity_blocks_resume_on_datasheet_change():
    original = datasheet(fingerprint="fp-v1", revision="1")
    same = datasheet(fingerprint="fp-v1", revision="1")
    changed = datasheet(fingerprint="fp-v2", revision="2")

    StorageCompatibilityAdapter.validate_resume_source(
        original,
        same,
    )

    with pytest.raises(SourceIdentityChangedError) as exc:
        StorageCompatibilityAdapter.validate_resume_source(
            original,
            changed,
        )
    assert exc.value.code == "SOURCE_IDENTITY_CHANGED"
    assert exc.value.expected.fingerprint == "fp-v1"
    assert exc.value.actual.fingerprint == "fp-v2"


def test_st03_storage_field_value_and_evidence_survive_runtime_unchanged(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    src = datasheet()
    business_result = StorageFieldResult(
        field_id="pe_cycle",
        status="FOUND",
        normalized_value=3000,
        unit="cycles",
        evidence=[field_evidence(src)],
        review_reason="datasheet explicit value",
    ).model_dump(mode="json")

    adapter = StorageCompatibilityAdapter(
        runtime,
        lambda _payload, _context: business_result,
    )
    output = adapter.execute(
        {"query": "extract pe cycle"},
        request_id="storage-evidence-1",
        partition_key="SSD-A",
    )
    result = output["runtime_result"]

    assert result.status == RuntimeStatus.COMPLETED
    assert result.data == business_result
    assert result.data["normalized_value"] == 3000
    assert result.data["unit"] == "cycles"
    assert result.data["evidence"][0]["source"]["fingerprint"] == "fp-v1"
    assert result.data["evidence"][0]["locator"]["value"] == {
        "page": 12,
        "table": "Endurance",
        "row": "P/E Cycle",
    }


def test_st04_golden_field_level_diff_requires_explain_or_accept():
    comparator = StorageGoldenFieldComparator()
    src = datasheet()
    expected = [
        StorageFieldResult(
            field_id="pe_cycle",
            status="FOUND",
            normalized_value=3000,
            unit="cycles",
            evidence=[field_evidence(src)],
            review_reason="explicit",
        ),
        StorageFieldResult(
            field_id="ecc",
            status="MISSING",
            normalized_value=None,
            unit=None,
            evidence=[],
            missing_reason="datasheet not declared",
        ),
    ]
    actual = [
        StorageFieldResult(
            field_id="pe_cycle",
            status="FOUND",
            normalized_value=5000,
            unit="cycles",
            evidence=[field_evidence(src)],
            review_reason="explicit",
        ),
        StorageFieldResult(
            field_id="ecc",
            status="MISSING",
            normalized_value=None,
            unit=None,
            evidence=[],
            missing_reason="datasheet not declared",
        ),
    ]

    # Found/Missing summaries are identical, but field-level value changed.
    with pytest.raises(StorageGoldenDiffError) as exc:
        comparator.compare(expected, actual)

    report = exc.value.report
    assert report.summary == {
        "FOUND": 1,
        "MISSING": 1,
        "CONFLICT": 0,
    }
    assert [
        (item.field_id, item.attribute)
        for item in report.diffs
        if not item.accepted
    ] == [("pe_cycle", "normalized_value")]

    accepted = comparator.compare(
        expected,
        actual,
        accepted_differences={
            "pe_cycle.normalized_value":
                "new datasheet revision raises qualified endurance value",
        },
    )
    assert accepted.passed is True
    assert len(accepted.diffs) == 1
    assert accepted.diffs[0].accepted is True
    assert accepted.diffs[0].explanation


def test_st04_golden_diff_checks_evidence_locator_and_review_reasons():
    comparator = StorageGoldenFieldComparator()
    expected_src = datasheet(fingerprint="fp-v1")
    actual_src = datasheet(fingerprint="fp-v2", revision="2")

    expected = [
        StorageFieldResult(
            field_id="write_limit",
            status="CONFLICT",
            normalized_value=10,
            unit="TB/day",
            evidence=[field_evidence(expected_src, page=5)],
            review_reason="manual review required",
            conflict_reason="two vendor tables disagree",
        )
    ]
    actual = [
        StorageFieldResult(
            field_id="write_limit",
            status="CONFLICT",
            normalized_value=10,
            unit="TB/day",
            evidence=[field_evidence(actual_src, page=6)],
            review_reason="auto accepted",
            conflict_reason="two vendor tables disagree",
        )
    ]

    with pytest.raises(StorageGoldenDiffError) as exc:
        comparator.compare(expected, actual)

    changed = {
        item.attribute
        for item in exc.value.report.diffs
    }
    assert changed == {
        "evidence_source_fingerprint",
        "evidence_locator",
        "review_reason",
    }


def test_st05_storage_what_stays_outside_runtime_how(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    calls = []

    def agent(payload, _context):
        calls.append(("agent", payload))
        return {
            "field_id": "pe_cycle",
            "raw_value": "3000 cycles",
            "evidence": [
                field_evidence().model_dump(mode="json")
            ],
        }

    def missing_verification(value):
        calls.append(("missing_verification", value["field_id"]))
        return {"missing": []}

    def normalizer(value):
        calls.append(("normalizer", value["raw_value"]))
        return {
            **value,
            "normalized_value": 3000,
            "unit": "cycles",
        }

    def business_validator(value):
        calls.append(("business_validator", value["normalized_value"]))
        return {"valid": value["normalized_value"] > 0}

    def review_gate(value, validation, missing):
        calls.append(("review_gate", validation["valid"]))
        return {
            "accepted": validation["valid"] and not missing["missing"],
            "reason": "storage business gate",
        }

    def reviewed_specification(value, review):
        calls.append(("reviewed_specification", review["accepted"]))
        return {
            "field_id": value["field_id"],
            "status": "FOUND" if review["accepted"] else "MISSING",
            "normalized_value": value["normalized_value"],
            "unit": value["unit"],
            "evidence": value["evidence"],
            "review_reason": review["reason"],
        }

    adapter = StorageCompatibilityAdapter(
        runtime,
        agent,
        missing_verification=missing_verification,
        normalizer=normalizer,
        business_validator=business_validator,
        review_gate=review_gate,
        reviewed_specification=reviewed_specification,
    )
    output = adapter.execute(
        {"document": "datasheet"},
        request_id="storage-what-how-1",
        partition_key="SSD-A",
    )

    assert output["runtime_result"].status == RuntimeStatus.COMPLETED
    assert output["reviewed_specification"]["status"] == "FOUND"
    assert [item[0] for item in calls] == [
        "agent",
        "normalizer",
        "missing_verification",
        "business_validator",
        "review_gate",
        "reviewed_specification",
    ]

    task = store.get_task(output["runtime_result"].task_id)
    snapshot = store.get_execution_snapshot(task.execution_snapshot_id)
    serialized = snapshot.model_dump_json()

    # Business WHAT components remain outside Runtime definitions/snapshot.
    for forbidden in (
        "MissingVerification",
        "Normalizer",
        "BusinessValidator",
        "ReviewGate",
        "ReviewedSpecification",
    ):
        assert forbidden not in serialized
