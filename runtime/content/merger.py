from __future__ import annotations

from typing import Protocol

from runtime.contracts import (
    CommittedPartialResult,
    MergeContext,
    MergeResult,
)
from runtime.content.evidence import EvidenceRegistry
from runtime.content.errors import InvalidPartialResultError, PartitionMismatchError


class ResultMerger(Protocol):
    def merge(
        self,
        inputs: list[CommittedPartialResult],
        context: MergeContext,
    ) -> MergeResult:
        ...


class ListResultMerger:
    """Generic loss-visible merger used by the Runtime PoC.

    Business-specific dedup/conflict semantics remain replaceable. This merger
    deliberately preserves each committed business payload as-is in a list.
    """

    def __init__(self, evidence_registry: EvidenceRegistry | None = None):
        self.evidence_registry = evidence_registry

    def merge(
        self,
        inputs: list[CommittedPartialResult],
        context: MergeContext,
    ) -> MergeResult:
        if any(not isinstance(item, CommittedPartialResult) for item in inputs):
            raise InvalidPartialResultError(
                "ResultMerger accepts committed partials only"
            )

        partitions = {item.partition_key for item in inputs}
        if context.partition_policy == "ISOLATED":
            if len(partitions) > 1:
                raise PartitionMismatchError(
                    "ISOLATED merge cannot mix partitions",
                    details={
                        "partitions": sorted(
                            "__none__" if value is None else value
                            for value in partitions
                        )
                    },
                )
            if (
                context.partition_key is not None
                and partitions
                and partitions != {context.partition_key}
            ):
                raise PartitionMismatchError(
                    "merge input partition does not match MergeContext",
                    details={
                        "expected_partition": context.partition_key,
                        "actual_partitions": list(partitions),
                    },
                )

        present_ids = {item.partial_id for item in inputs}
        expected_ids = set(context.expected_partial_ids)
        missing = sorted(expected_ids - present_ids)

        evidence = []
        if self.evidence_registry is not None:
            seen: set[str] = set()
            for partial in inputs:
                for evidence_id in partial.evidence_ids:
                    if evidence_id in seen:
                        continue
                    seen.add(evidence_id)
                    evidence.append(self.evidence_registry.get(evidence_id))

        return MergeResult(
            merge_key=context.merge_key,
            data=[item.data for item in inputs],
            evidence=evidence,
            derived_from_partial_ids=[item.partial_id for item in inputs],
            complete=not missing,
            missing_partial_ids=missing,
            warnings=[] if not missing else ["MISSING_COMMITTED_PARTIAL"],
            metadata={
                "input_count": len(inputs),
                "expected_count": len(expected_ids),
                "source_partitions": sorted(
                    "__none__" if value is None else value
                    for value in partitions
                ),
                "partition_policy": context.partition_policy,
            },
        )


class MergeCoordinator:
    def __init__(self, store):
        self.store = store

    def merge_and_commit(
        self,
        merger: ResultMerger,
        inputs: list[CommittedPartialResult],
        context: MergeContext,
    ) -> MergeResult:
        existing = self.store.get_merge_result(context.merge_key)
        if existing is not None:
            return existing

        result = merger.merge(inputs, context)
        return self.store.commit_merge_result(result)
