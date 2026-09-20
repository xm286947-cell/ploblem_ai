from __future__ import annotations

from runtime import (
    EvidenceLocator,
    EvidenceReference,
    LightweightExecutionEngine,
    RuntimeStatus,
    SourceRef,
    SqliteTaskStore,
)
from runtime.adapters import (
    MajorIssueD01RuntimeAdapter,
    MajorIssueObjectSpec,
    RepeatCaseRuntimeAdapter,
)


def source(partition="P1"):
    return SourceRef(
        source_id=f"major-doc-{partition}",
        source_type="MAJOR_REVIEW",
        revision="1",
        content_hash=f"hash-{partition}",
        fingerprint=f"fp-{partition}",
        uri=f"major://{partition}/review",
    )


def specs(count=4):
    return [
        MajorIssueObjectSpec(
            object_id=f"obj-{i}",
            unit_id=f"unit-{i}",
            locator={"section": f"section-{i}"},
        )
        for i in range(1, count + 1)
    ]


def evidence(src, object_id, partition):
    return EvidenceReference(
        evidence_id=f"e-{partition}-{object_id}",
        source=src,
        locator=EvidenceLocator(
            type="SECTION",
            value={"object_id": object_id},
        ),
        excerpt=f"evidence for {object_id}",
        partition_key=partition,
    )


def truncated_provider(src, partition, calls):
    def provider(provider_input, pending_specs, context):
        calls.append(
            {
                "pending": [item["object_id"] for item in pending_specs],
                "committed": list(
                    context["d01"]["committed_object_ids"]
                ),
                "provider_input": provider_input,
            }
        )
        result = []
        for item in pending_specs:
            object_id = item["object_id"]
            if object_id == "obj-4":
                result.append(
                    {
                        "object_id": object_id,
                        "data": {"id": object_id, "text": "truncated"},
                        "schema_valid": True,
                        "complete_object": False,
                        "finish_reason": "length",
                        "evidence": [
                            evidence(src, object_id, partition).model_dump(
                                mode="json"
                            )
                        ],
                    }
                )
            else:
                result.append(
                    {
                        "object_id": object_id,
                        "data": {"id": object_id, "text": "complete"},
                        "schema_valid": True,
                        "complete_object": True,
                        "finish_reason": "stop",
                        "evidence": [
                            evidence(src, object_id, partition).model_dump(
                                mode="json"
                            )
                        ],
                    }
                )
        return result

    return provider


def completion_provider(src, partition, calls):
    def provider(provider_input, pending_specs, context):
        calls.append(
            {
                "pending": [item["object_id"] for item in pending_specs],
                "committed": list(
                    context["d01"]["committed_object_ids"]
                ),
                "provider_input": provider_input,
            }
        )
        return [
            {
                "object_id": item["object_id"],
                "data": {
                    "id": item["object_id"],
                    "text": f"complete-{partition}",
                },
                "schema_valid": True,
                "complete_object": True,
                "finish_reason": "stop",
                "evidence": [
                    evidence(
                        src,
                        item["object_id"],
                        partition,
                    ).model_dump(mode="json")
                ],
            }
            for item in pending_specs
        ]

    return provider


def test_m01_d01_output_truncation_commits_first_three_only(tmp_path):
    db_path = tmp_path / "runtime.db"
    store = SqliteTaskStore(db_path)
    runtime = LightweightExecutionEngine(store)
    src = source("P1")
    calls = []
    adapter = MajorIssueD01RuntimeAdapter(
        runtime,
        store,
        truncated_provider(src, "P1", calls),
    )

    outcome = adapter.execute_partition(
        case_id="CASE-D01",
        issue_version_id="V1",
        partition_key="P1",
        source=src,
        expected_objects=specs(),
        provider_input={"document": "long-major-review"},
    )

    assert outcome.status == RuntimeStatus.PARTIAL
    assert outcome.business_consumable is False
    assert outcome.provider_calls == 1
    assert calls == [
        {
            "pending": ["obj-1", "obj-2", "obj-3", "obj-4"],
            "committed": [],
            "provider_input": {"document": "long-major-review"},
        }
    ]

    assert [item["object_id"] for item in outcome.committed_objects] == [
        "obj-1",
        "obj-2",
        "obj-3",
    ]
    assert all(item["execution_key"] for item in outcome.committed_objects)
    assert len(
        {item["execution_key"] for item in outcome.committed_objects}
    ) == 3
    assert "obj-4" not in {
        item["object_id"] for item in outcome.committed_objects
    }
    assert outcome.coverage.coverage_ratio == 0.75
    assert outcome.coverage.complete is False
    assert outcome.merge is None
    assert outcome.gate is None


def test_m02_process_restart_resumes_only_unfinished_object(tmp_path):
    db_path = tmp_path / "runtime.db"
    src = source("P1")
    first_calls = []

    store1 = SqliteTaskStore(db_path)
    runtime1 = LightweightExecutionEngine(store1)
    adapter1 = MajorIssueD01RuntimeAdapter(
        runtime1,
        store1,
        truncated_provider(src, "P1", first_calls),
    )
    first = adapter1.execute_partition(
        case_id="CASE-D01",
        issue_version_id="V1",
        partition_key="P1",
        source=src,
        expected_objects=specs(),
        provider_input={"document": "long-major-review"},
    )
    assert first.status == RuntimeStatus.PARTIAL

    original_partials = {
        item["object_id"]: (
            item["partial_id"],
            item["execution_key"],
        )
        for item in first.committed_objects
    }

    second_calls = []
    store2 = SqliteTaskStore(db_path)
    runtime2 = LightweightExecutionEngine(store2)
    adapter2 = MajorIssueD01RuntimeAdapter(
        runtime2,
        store2,
        completion_provider(src, "P1", second_calls),
    )
    resumed = adapter2.resume(first.task_id)

    assert resumed.status == RuntimeStatus.COMPLETED
    assert resumed.provider_calls == 2
    assert second_calls == [
        {
            "pending": ["obj-4"],
            "committed": ["obj-1", "obj-2", "obj-3"],
            "provider_input": {"document": "long-major-review"},
        }
    ]

    after = {
        item["object_id"]: (
            item["partial_id"],
            item["execution_key"],
        )
        for item in resumed.committed_objects
    }
    for object_id in ("obj-1", "obj-2", "obj-3"):
        assert after[object_id] == original_partials[object_id]
    assert set(after) == {"obj-1", "obj-2", "obj-3", "obj-4"}

    runs = store2.list_runs(first.task_id)
    assert len(runs) == 2
    assert runs[1].resume_of_run_id == runs[0].run_id


def test_m03_final_completion_requires_coverage_merge_evidence_and_gate(tmp_path):
    db_path = tmp_path / "runtime.db"
    src = source("P1")

    store = SqliteTaskStore(db_path)
    runtime = LightweightExecutionEngine(store)
    first_calls = []
    adapter = MajorIssueD01RuntimeAdapter(
        runtime,
        store,
        truncated_provider(src, "P1", first_calls),
    )
    first = adapter.execute_partition(
        case_id="CASE-D01-FINAL",
        issue_version_id="V1",
        partition_key="P1",
        source=src,
        expected_objects=specs(),
    )
    assert first.status == RuntimeStatus.PARTIAL

    final_calls = []
    adapter.provider = completion_provider(src, "P1", final_calls)
    completed = adapter.resume(first.task_id)

    assert completed.status == RuntimeStatus.COMPLETED
    assert completed.business_consumable is True
    assert completed.coverage.coverage_ratio == 1.0
    assert completed.coverage.complete is True
    assert completed.merge is not None
    assert completed.merge.complete is True
    assert completed.merge.missing_partial_ids == []
    assert completed.evidence_integrity is True
    assert completed.gate is not None
    assert completed.gate.passed is True
    assert completed.gate.reasons == []

    raw_ids = {
        f"e-P1-obj-{i}"
        for i in range(1, 5)
    }
    assert raw_ids.issubset(set(completed.evidence_lineage_ids))
    assert any(
        item.startswith("evidence-d01-merge-")
        for item in completed.evidence_lineage_ids
    )


def test_m04_two_partitions_keep_data_evidence_coverage_and_merge_isolated(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)

    calls = []

    def provider(provider_input, pending_specs, context):
        partition = context["d01"]["partition_key"]
        src = source(partition)
        calls.append((partition, [x["object_id"] for x in pending_specs]))
        return [
            {
                "object_id": item["object_id"],
                "data": {
                    "partition": partition,
                    "object_id": item["object_id"],
                },
                "schema_valid": True,
                "complete_object": True,
                "evidence": [
                    evidence(
                        src,
                        item["object_id"],
                        partition,
                    ).model_dump(mode="json")
                ],
            }
            for item in pending_specs
        ]

    adapter = MajorIssueD01RuntimeAdapter(runtime, store, provider)
    outcome_a = adapter.execute_partition(
        case_id="CASE-MULTI",
        issue_version_id="V1",
        partition_key="A",
        source=source("A"),
        expected_objects=specs(2),
    )
    outcome_b = adapter.execute_partition(
        case_id="CASE-MULTI",
        issue_version_id="V1",
        partition_key="B",
        source=source("B"),
        expected_objects=specs(2),
    )

    assert outcome_a.status == RuntimeStatus.COMPLETED
    assert outcome_b.status == RuntimeStatus.COMPLETED
    assert outcome_a.task_id != outcome_b.task_id
    assert outcome_a.coverage.partition_key == "A"
    assert outcome_b.coverage.partition_key == "B"
    assert outcome_a.merge.metadata["source_partitions"] == ["A"]
    assert outcome_b.merge.metadata["source_partitions"] == ["B"]

    assert {
        item["data"]["partition"]
        for item in outcome_a.committed_objects
    } == {"A"}
    assert {
        item["data"]["partition"]
        for item in outcome_b.committed_objects
    } == {"B"}

    keys_a = {
        item["execution_key"]
        for item in outcome_a.committed_objects
    }
    keys_b = {
        item["execution_key"]
        for item in outcome_b.committed_objects
    }
    assert keys_a.isdisjoint(keys_b)

    evidence_a = set(outcome_a.evidence_lineage_ids)
    evidence_b = set(outcome_b.evidence_lineage_ids)
    assert "e-A-obj-1" in evidence_a
    assert "e-B-obj-1" in evidence_b
    assert "e-B-obj-1" not in evidence_a
    assert "e-A-obj-1" not in evidence_b
    assert calls == [
        ("A", ["obj-1", "obj-2"]),
        ("B", ["obj-1", "obj-2"]),
    ]


def test_m05_repeat_case_business_schema_remains_opaque_to_runtime(tmp_path):
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = LightweightExecutionEngine(store)
    seen = []

    def repeat_case_agent(payload, context):
        seen.append(payload)
        return {
            "business_result": {
                "retrieval": payload.get("retrieval"),
                "ranking": payload.get("ranking"),
                "threshold": payload.get("threshold"),
                "judgement": payload.get("judgement"),
            },
            "runtime_execution_key": context["runtime"]["execution_key"],
        }

    adapter = RepeatCaseRuntimeAdapter(runtime, repeat_case_agent)
    payload = {
        "retrieval": {
            "candidate_ids": ["CASE-OLD-1", "CASE-OLD-2"],
            "source_scope": "AUTHORIZED_HISTORY",
        },
        "ranking": [{"id": "CASE-OLD-1", "score": 0.91}],
        "threshold": {"similarity": 0.82},
        "judgement": {
            "decision": "PENDING_HUMAN_REVIEW",
            "reason": "business-owned",
        },
    }

    result = adapter.execute(
        payload,
        request_id="repeat-case-opaque-1",
        partition_key="EVENT-1",
    )

    assert result.status == RuntimeStatus.COMPLETED
    assert seen == [payload]
    assert result.data["business_result"] == payload
    assert result.data["runtime_execution_key"]

    # A completely different business payload also runs without changing Runtime.
    second_payload = {
        "candidate_graph": [{"node": "X"}],
        "policy_bundle": {"custom": True},
    }
    second = adapter.execute(
        second_payload,
        request_id="repeat-case-opaque-2",
        partition_key="EVENT-2",
    )
    assert second.status == RuntimeStatus.COMPLETED
    assert seen[-1] == second_payload
