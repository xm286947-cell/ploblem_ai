from __future__ import annotations

import pytest

from runtime import (
    AtomicGroup,
    AtomicGroupPolicy,
    ContentSource,
    ContentStrategyDefinition,
    LogicalUnit,
    LongContentPolicy,
    PartialResultCandidate,
    SourceBundle,
    SourceRef,
)
from runtime.content import (
    AtomicUnitTooLargeError,
    CallableContentProjector,
    ContentPlanner,
    ContentProjectionRequiredError,
    ContentStrategyRegistry,
    InvalidPartialResultError,
    PartialResultCommitter,
    SourceBundleIdentityProjector,
)


def source(source_id="src-1"):
    return SourceRef(
        source_id=source_id,
        source_type="BUSINESS_RECORD",
        revision="1",
        content_hash=f"hash-{source_id}",
        fingerprint=f"fp-{source_id}",
    )


def test_c01_structured_input_requires_explicit_content_projection():
    registry = ContentStrategyRegistry()
    registry.register(
        ContentStrategyDefinition(
            strategy_id="quality_issue",
            version="1",
            projector_ref="quality_issue.projector.v1",
            planner_ref="default.planner.v1",
        )
    )

    def project_quality_issue(payload, _context, _definition):
        src = source("issue-1")
        return SourceBundle(
            bundle_id="bundle-issue-1",
            sources=[ContentSource(source=src)],
            logical_units=[
                LogicalUnit(
                    unit_id="problem",
                    source_id=src.source_id,
                    inline_payload=payload["problem"],
                ),
                LogicalUnit(
                    unit_id="root_cause",
                    source_id=src.source_id,
                    inline_payload=payload["root_cause"],
                ),
            ],
        )

    registry.bind_projector(
        "quality_issue.projector.v1",
        CallableContentProjector(project_quality_issue),
    )
    bundle = registry.project(
        "quality_issue@1",
        {
            "problem": "PLC restart",
            "root_cause": "configuration mismatch",
            "arbitrary_nested_field": {"runtime_must_not_guess": True},
        },
    )

    plan = ContentPlanner().plan(
        bundle,
        LongContentPolicy(max_units_per_chunk=1),
        strategy_ref="quality_issue@1",
    )
    assert [chunk.unit_ids for chunk in plan.chunks] == [
        ["problem"],
        ["root_cause"],
    ]

    with pytest.raises(ContentProjectionRequiredError):
        SourceBundleIdentityProjector().project(
            {"problem": "runtime must not introspect this dict"},
            {},
        )


def test_c02_keep_together_is_never_split_and_fails_if_atomic_group_too_large():
    src = source("datasheet")
    bundle = SourceBundle(
        bundle_id="storage-linked-fields",
        sources=[ContentSource(source=src)],
        logical_units=[
            LogicalUnit(unit_id="value", source_id=src.source_id, inline_payload="3000"),
            LogicalUnit(unit_id="unit", source_id=src.source_id, inline_payload="cycles"),
            LogicalUnit(unit_id="footnote", source_id=src.source_id, inline_payload="at 25C"),
            LogicalUnit(unit_id="next", source_id=src.source_id, inline_payload="other"),
        ],
        atomic_groups=[
            AtomicGroup(
                group_id="pe-cycle-field",
                unit_ids=["value", "unit", "footnote"],
                policy=AtomicGroupPolicy.KEEP_TOGETHER,
                grouping_hint="linked_fields",
            )
        ],
    )

    plan = ContentPlanner().plan(
        bundle,
        LongContentPolicy(max_units_per_chunk=3, max_payload_chars=1000),
    )
    assert plan.chunks[0].unit_ids == ["value", "unit", "footnote"]
    assert "next" not in plan.chunks[0].unit_ids

    with pytest.raises(AtomicUnitTooLargeError) as exc:
        ContentPlanner().plan(
            bundle,
            LongContentPolicy(max_units_per_chunk=2, max_payload_chars=1000),
        )
    assert exc.value.code == "ATOMIC_UNIT_TOO_LARGE"


def test_c03_same_context_can_split_but_every_chunk_carries_declared_context():
    src = source("major-issue")
    units = [
        LogicalUnit(
            unit_id=f"section-{i}",
            source_id=src.source_id,
            inline_payload={"section": i},
        )
        for i in range(1, 5)
    ]
    bundle = SourceBundle(
        bundle_id="major-issue-long",
        sources=[ContentSource(source=src)],
        logical_units=units,
        atomic_groups=[
            AtomicGroup(
                group_id="same-itr-context",
                unit_ids=[item.unit_id for item in units],
                policy=AtomicGroupPolicy.SAME_CONTEXT,
                grouping_hint="itr_context",
            )
        ],
        shared_context={
            "itr_context": {
                "itr_id": "ITR-DEMO",
                "product": "PLC",
            }
        },
    )

    plan = ContentPlanner().plan(
        bundle,
        LongContentPolicy(max_units_per_chunk=2, max_payload_chars=1000),
    )

    assert len(plan.chunks) == 2
    assert [chunk.unit_ids for chunk in plan.chunks] == [
        ["section-1", "section-2"],
        ["section-3", "section-4"],
    ]
    for chunk in plan.chunks:
        assert chunk.shared_context["itr_context"]["itr_id"] == "ITR-DEMO"


def test_d4_overlap_is_context_not_primary_coverage_unit():
    src = source("long-text")
    bundle = SourceBundle(
        bundle_id="overlap-demo",
        sources=[ContentSource(source=src)],
        logical_units=[
            LogicalUnit(
                unit_id=f"u{i}",
                source_id=src.source_id,
                inline_payload=f"text-{i}",
            )
            for i in range(1, 5)
        ],
    )

    plan = ContentPlanner().plan(
        bundle,
        LongContentPolicy(
            max_units_per_chunk=3,
            max_payload_chars=1000,
            overlap_units=1,
        ),
    )

    assert len(plan.chunks) == 2
    assert plan.chunks[0].unit_ids == ["u1", "u2", "u3"]
    assert plan.chunks[0].overlap_unit_ids == []
    assert plan.chunks[1].unit_ids == ["u4"]
    assert plan.chunks[1].overlap_unit_ids == ["u3"]
    assert "u3" not in plan.chunks[1].unit_ids


def test_d4_plan_is_deterministic_for_resume_stability():
    src = source("stable")
    bundle = SourceBundle(
        bundle_id="stable-bundle",
        sources=[ContentSource(source=src)],
        logical_units=[
            LogicalUnit(
                unit_id=f"u{i}",
                source_id=src.source_id,
                inline_payload=i,
            )
            for i in range(5)
        ],
    )
    policy = LongContentPolicy(max_units_per_chunk=2, overlap_units=1)

    first = ContentPlanner().plan(bundle, policy, strategy_ref="demo@1")
    second = ContentPlanner().plan(bundle, policy, strategy_ref="demo@1")

    assert first.plan_id == second.plan_id
    assert [item.chunk_id for item in first.chunks] == [
        item.chunk_id for item in second.chunks
    ]


def test_d4_truncated_or_schema_invalid_partial_cannot_be_committed():
    committer = PartialResultCommitter()

    with pytest.raises(InvalidPartialResultError):
        committer.commit(
            PartialResultCandidate(
                chunk_id="chunk-1",
                execution_key="exec-1",
                unit_ids=["u1"],
                data={"object": "truncated"},
                schema_valid=True,
                complete_object=False,
                finish_reason="length",
            )
        )

    with pytest.raises(InvalidPartialResultError):
        committer.commit(
            PartialResultCandidate(
                chunk_id="chunk-1",
                execution_key="exec-1",
                unit_ids=["u1"],
                data={"object": "bad schema"},
                schema_valid=False,
                complete_object=True,
            )
        )

    committed = committer.commit(
        PartialResultCandidate(
            chunk_id="chunk-1",
            execution_key="exec-1",
            unit_ids=["u1"],
            data={"object": "independently complete"},
            schema_valid=True,
            complete_object=True,
            finish_reason="length",
        )
    )
    assert committed.execution_key == "exec-1"
    assert committed.metadata["independently_validated"] is True
