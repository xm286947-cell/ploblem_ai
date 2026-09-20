from __future__ import annotations

from datetime import datetime, timezone

import pytest

from runtime import (
    CommittedPartialResult,
    CoverageUnit,
    CoverageUniverse,
    EvidenceLocator,
    EvidenceReference,
    MergeContext,
    MergeResult,
    Range,
    SourceRef,
)
from runtime.content import (
    CompletenessGateEvaluator,
    CoverageCalculator,
    EvidenceRegistry,
    ListResultMerger,
    MergeCoordinator,
    SourceIdentityChangedError,
    SourceIdentityProvider,
)
from runtime.store import SqliteTaskStore


def source(*, fingerprint="fp-v1", revision="1", uri="file://same-name.pdf"):
    return SourceRef(
        source_id="source-1",
        source_type="PDF",
        revision=revision,
        content_hash=f"hash-{fingerprint}",
        fingerprint=fingerprint,
        uri=uri,
    )


def partial(partial_id, data, *, evidence_ids=None):
    return CommittedPartialResult(
        partial_id=partial_id,
        chunk_id=f"chunk-{partial_id}",
        execution_key=f"exec-{partial_id}",
        unit_ids=[f"unit-{partial_id}"],
        data=data,
        evidence_ids=evidence_ids or [],
        committed_at=datetime.now(timezone.utc),
    )


def test_e01_range_overlap_uses_union_not_chunk_count():
    universe = CoverageUniverse(
        source=source(),
        coverage_type="RANGE",
        range_targets=[Range(start=0, end=100)],
        universe_fingerprint="universe-v1",
    )

    coverage = CoverageCalculator().calculate(
        universe,
        processed_ranges=[
            Range(start=0, end=60),
            Range(start=50, end=100),
            Range(start=0, end=60),
        ],
    )

    assert coverage.coverage_ratio == 1.0
    assert coverage.complete is True
    assert [(item.start, item.end) for item in coverage.processed_ranges] == [
        (0, 100)
    ]


def test_e02_duplicate_retry_discrete_unit_counts_once():
    universe = CoverageUniverse(
        source=source(),
        coverage_type="ITEM",
        unit_targets=[
            CoverageUnit(unit_id="u1"),
            CoverageUnit(unit_id="u2"),
        ],
        universe_fingerprint="items-v1",
    )

    coverage = CoverageCalculator().calculate(
        universe,
        processed_unit_ids=["u1", "u1", "u2", "u2"],
    )

    assert coverage.coverage_ratio == 1.0
    assert coverage.processed_units == ["u1", "u2"]
    assert coverage.complete is True


def test_e03_missing_required_unit_keeps_coverage_incomplete():
    universe = CoverageUniverse(
        source=source(),
        coverage_type="SECTION",
        unit_targets=[
            CoverageUnit(unit_id="s1"),
            CoverageUnit(unit_id="s2"),
        ],
        universe_fingerprint="sections-v1",
    )

    coverage = CoverageCalculator().calculate(
        universe,
        processed_unit_ids=["s1"],
    )

    assert coverage.coverage_ratio == 0.5
    assert coverage.pending_units == ["s2"]
    assert coverage.complete is False


def test_e04_e05_source_identity_change_blocks_resume_even_same_uri():
    expected = source(fingerprint="fp-v1", revision="1")
    changed = source(fingerprint="fp-v2", revision="2")

    with pytest.raises(SourceIdentityChangedError) as exc:
        SourceIdentityProvider.validate_same_identity(expected, changed)

    assert exc.value.code == "SOURCE_IDENTITY_CHANGED"


def test_e06_e07_evidence_version_and_lineage_are_stable():
    registry = EvidenceRegistry()
    old_source = source(fingerprint="fp-v1", revision="1")
    new_source = source(fingerprint="fp-v2", revision="2")

    e1 = registry.save(
        EvidenceReference(
            evidence_id="e1",
            source=old_source,
            locator=EvidenceLocator(type="PAGE", value={"page": 1}),
            excerpt="old version evidence",
        )
    )
    e2 = registry.save(
        EvidenceReference(
            evidence_id="e2",
            source=old_source,
            locator=EvidenceLocator(type="PAGE", value={"page": 2}),
        )
    )
    derived = registry.derive(
        evidence_id="e3",
        source=old_source,
        locator=EvidenceLocator(type="SECTION", value={"name": "merged"}),
        derived_from=["e1", "e2"],
    )

    assert registry.get("e1").source.fingerprint == "fp-v1"
    assert new_source.fingerprint == "fp-v2"
    assert {item.evidence_id for item in registry.lineage(derived.evidence_id)} == {
        "e1",
        "e2",
        "e3",
    }
    assert registry.validate_integrity(["e3"]) is True
    assert e1.source.revision == "1"


def test_e08_merger_preserves_business_field_level_evidence_payload():
    business_payload = {
        "field_id": "pe_cycle",
        "value": 3000,
        "unit": "cycles",
        "evidence": {
            "source": "datasheet",
            "locator": {"page": 12, "table": "Endurance"},
        },
    }
    p1 = partial("p1", business_payload)
    result = ListResultMerger().merge(
        [p1],
        MergeContext(
            merge_key="merge-business-evidence",
            expected_partial_ids=["p1"],
        ),
    )

    assert result.complete is True
    assert result.data[0] == business_payload
    assert result.data[0]["evidence"]["locator"]["page"] == 12


def test_c04_merge_retry_then_idempotent_commit(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    coordinator = MergeCoordinator(store)
    p1 = partial("p1", {"id": 1})
    p2 = partial("p2", {"id": 2})
    context = MergeContext(
        merge_key="merge-retry-key",
        expected_partial_ids=["p1", "p2"],
    )

    class FailOnceMerger:
        def __init__(self):
            self.calls = 0
            self.delegate = ListResultMerger()

        def merge(self, inputs, merge_context):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("injected merge failure")
            return self.delegate.merge(inputs, merge_context)

    merger = FailOnceMerger()

    with pytest.raises(RuntimeError):
        coordinator.merge_and_commit(merger, [p1, p2], context)

    second = coordinator.merge_and_commit(merger, [p1, p2], context)
    third = coordinator.merge_and_commit(merger, [p1, p2], context)

    assert second.complete is True
    assert third == second
    assert merger.calls == 2
    assert store.get_merge_result("merge-retry-key") == second


def test_c06_g10_merge_loss_and_integrity_fail_completeness_gate():
    p1 = partial("p1", {"id": 1})
    p2 = partial("p2", {"id": 2})
    merge = ListResultMerger().merge(
        [p1, p2],
        MergeContext(
            merge_key="merge-loss",
            expected_partial_ids=["p1", "p2", "p3"],
        ),
    )
    assert merge.complete is False
    assert merge.missing_partial_ids == ["p3"]

    universe = CoverageUniverse(
        source=source(),
        coverage_type="ITEM",
        unit_targets=[CoverageUnit(unit_id="u1")],
        universe_fingerprint="gate-v1",
    )
    coverage = CoverageCalculator().calculate(
        universe,
        processed_unit_ids=["u1"],
    )
    assert coverage.complete is True

    gate = CompletenessGateEvaluator().evaluate(
        coverage=coverage,
        schema_valid=True,
        merge_result=merge,
        evidence_integrity=True,
        business_gate_passed=True,
    )
    assert gate.passed is False
    assert "MERGE_INCOMPLETE" in gate.reasons

    evidence_gate = CompletenessGateEvaluator().evaluate(
        coverage=coverage,
        schema_valid=True,
        merge_complete=True,
        evidence_integrity=False,
        business_gate_passed=True,
    )
    assert evidence_gate.passed is False
    assert "EVIDENCE_INTEGRITY_FAILED" in evidence_gate.reasons

    business_gate = CompletenessGateEvaluator().evaluate(
        coverage=coverage,
        schema_valid=True,
        merge_complete=True,
        evidence_integrity=True,
        business_gate_passed=False,
    )
    assert business_gate.passed is False
    assert "BUSINESS_GATE_FAILED" in business_gate.reasons
