from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from runtime.contracts import (
    AtomicGroup,
    AtomicGroupPolicy,
    ContentChunk,
    ContentPlan,
    LogicalUnit,
    LongContentPolicy,
    SourceBundle,
)
from runtime.content.errors import (
    AtomicGroupPartitionMismatchError,
    AtomicUnitTooLargeError,
    ContentCoreError,
    ContentProjectionRequiredError,
    MissingSharedContextError,
)


def _stable_id(prefix: str, value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return f"{prefix}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


class ContentPlanner:
    """Deterministic D4 planner over an explicit SourceBundle.

    It never inspects arbitrary business dict/list input. Business semantics must
    already be projected into LogicalUnit/AtomicGroup declarations.
    """

    def plan(
        self,
        bundle: SourceBundle,
        policy: LongContentPolicy | dict[str, Any] | None = None,
        *,
        strategy_ref: str | None = None,
    ) -> ContentPlan:
        if not isinstance(bundle, SourceBundle):
            raise ContentProjectionRequiredError(
                "ContentPlanner requires SourceBundle; use ContentProjector first",
                details={"input_type": type(bundle).__name__},
            )

        if policy is None:
            resolved_policy = LongContentPolicy()
        elif isinstance(policy, LongContentPolicy):
            resolved_policy = policy
        else:
            resolved_policy = LongContentPolicy.model_validate(policy)

        if resolved_policy.max_units_per_chunk < 1:
            raise ContentCoreError(
                "max_units_per_chunk must be >= 1",
                code="INVALID_LONG_CONTENT_POLICY",
            )
        if resolved_policy.max_payload_chars < 1:
            raise ContentCoreError(
                "max_payload_chars must be >= 1",
                code="INVALID_LONG_CONTENT_POLICY",
            )
        if resolved_policy.overlap_units < 0:
            raise ContentCoreError(
                "overlap_units must be >= 0",
                code="INVALID_LONG_CONTENT_POLICY",
            )

        units = list(bundle.logical_units)
        unit_by_id: dict[str, LogicalUnit] = {}
        unit_order: dict[str, int] = {}
        for index, unit in enumerate(units):
            if unit.unit_id in unit_by_id:
                raise ContentCoreError(
                    f"duplicate LogicalUnit id: {unit.unit_id}",
                    code="DUPLICATE_LOGICAL_UNIT",
                )
            unit_by_id[unit.unit_id] = unit
            unit_order[unit.unit_id] = index

        source_partition = {
            item.source.source_id: item.partition_key
            for item in bundle.sources
        }

        def partition_for(unit: LogicalUnit) -> str | None:
            return (
                unit.partition_key
                or source_partition.get(unit.source_id)
                or bundle.default_partition_key
            )

        keep_group_by_unit: dict[str, AtomicGroup] = {}
        same_context_groups: list[AtomicGroup] = []

        for group in bundle.atomic_groups:
            missing = [uid for uid in group.unit_ids if uid not in unit_by_id]
            if missing:
                raise ContentCoreError(
                    f"atomic group {group.group_id} references unknown units",
                    code="ATOMIC_GROUP_UNKNOWN_UNIT",
                    details={"group_id": group.group_id, "missing": missing},
                )
            if group.policy == AtomicGroupPolicy.KEEP_TOGETHER:
                partitions = {
                    partition_for(unit_by_id[uid])
                    for uid in group.unit_ids
                }
                if len(partitions) > 1:
                    raise AtomicGroupPartitionMismatchError(
                        f"KEEP_TOGETHER group crosses partitions: {group.group_id}",
                        details={
                            "group_id": group.group_id,
                            "partitions": sorted(
                                "__none__" if value is None else value
                                for value in partitions
                            ),
                        },
                    )
                for uid in group.unit_ids:
                    existing = keep_group_by_unit.get(uid)
                    if existing and existing.group_id != group.group_id:
                        raise ContentCoreError(
                            f"LogicalUnit belongs to multiple KEEP_TOGETHER groups: {uid}",
                            code="OVERLAPPING_KEEP_TOGETHER_GROUP",
                            details={
                                "unit_id": uid,
                                "groups": [existing.group_id, group.group_id],
                            },
                        )
                    keep_group_by_unit[uid] = group
            elif group.policy == AtomicGroupPolicy.SAME_CONTEXT:
                same_context_groups.append(group)

        components_by_partition: dict[str | None, list[list[str]]] = defaultdict(list)
        partition_order: list[str | None] = []
        seen_units: set[str] = set()

        for unit in units:
            if unit.unit_id in seen_units:
                continue
            partition_key = partition_for(unit)
            if partition_key not in partition_order:
                partition_order.append(partition_key)

            keep_group = keep_group_by_unit.get(unit.unit_id)
            if keep_group is None:
                component = [unit.unit_id]
            else:
                component = sorted(
                    keep_group.unit_ids,
                    key=lambda uid: unit_order[uid],
                )
            component_partitions = {
                partition_for(unit_by_id[uid])
                for uid in component
            }
            if component_partitions != {partition_key}:
                raise AtomicGroupPartitionMismatchError(
                    "atomic component cannot cross partitions",
                    details={
                        "unit_ids": component,
                        "partitions": list(component_partitions),
                    },
                )
            components_by_partition[partition_key].append(component)
            seen_units.update(component)

        def unit_size(uid: str) -> int:
            unit = unit_by_id[uid]
            if "estimated_payload_chars" in unit.metadata:
                return max(0, int(unit.metadata["estimated_payload_chars"]))
            if unit.inline_payload is None:
                return 0
            return len(
                json.dumps(
                    unit.inline_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                    separators=(",", ":"),
                )
            )

        def context_for(unit_ids: list[str]) -> dict[str, Any]:
            refs: set[str] = set()
            unit_set = set(unit_ids)
            for uid in unit_ids:
                refs.update(unit_by_id[uid].context_refs)

            for group in same_context_groups:
                if unit_set.intersection(group.unit_ids):
                    if group.grouping_hint:
                        refs.add(group.grouping_hint)
                    else:
                        refs.update(bundle.shared_context.keys())

            missing = sorted(ref for ref in refs if ref not in bundle.shared_context)
            if missing:
                raise MissingSharedContextError(
                    "declared shared context is missing from SourceBundle",
                    details={"missing_context_refs": missing},
                )
            return {
                ref: bundle.shared_context[ref]
                for ref in sorted(refs)
            }

        raw_chunks: list[dict[str, Any]] = []
        for partition_key in partition_order:
            current: list[str] = []
            current_size = 0

            for component in components_by_partition[partition_key]:
                component_size = sum(unit_size(uid) for uid in component)
                if (
                    len(component) > resolved_policy.max_units_per_chunk
                    or component_size > resolved_policy.max_payload_chars
                ):
                    group = keep_group_by_unit.get(component[0])
                    raise AtomicUnitTooLargeError(
                        "atomic unit/group exceeds provider planning capacity",
                        details={
                            "group_id": group.group_id if group else None,
                            "unit_ids": component,
                            "unit_count": len(component),
                            "payload_chars": component_size,
                            "max_units_per_chunk": resolved_policy.max_units_per_chunk,
                            "max_payload_chars": resolved_policy.max_payload_chars,
                        },
                    )

                would_exceed = bool(current) and (
                    len(current) + len(component)
                    > resolved_policy.max_units_per_chunk
                    or current_size + component_size
                    > resolved_policy.max_payload_chars
                )
                if would_exceed:
                    raw_chunks.append(
                        {
                            "partition_key": partition_key,
                            "unit_ids": list(current),
                        }
                    )
                    current = []
                    current_size = 0

                current.extend(component)
                current_size += component_size

            if current:
                raw_chunks.append(
                    {
                        "partition_key": partition_key,
                        "unit_ids": list(current),
                    }
                )

        chunks_by_partition: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
        for item in raw_chunks:
            chunks_by_partition[item["partition_key"]].append(item)

        final_chunks: list[ContentChunk] = []
        for partition_key in partition_order:
            history: list[str] = []
            for item in chunks_by_partition[partition_key]:
                primary = list(item["unit_ids"])
                desired_overlap = (
                    history[-resolved_policy.overlap_units :]
                    if resolved_policy.overlap_units
                    else []
                )

                expanded_overlap: set[str] = set()
                for uid in desired_overlap:
                    group = keep_group_by_unit.get(uid)
                    if group is None:
                        expanded_overlap.add(uid)
                    else:
                        expanded_overlap.update(group.unit_ids)

                overlap = [
                    uid
                    for uid in sorted(expanded_overlap, key=lambda value: unit_order[value])
                    if uid not in primary
                ]
                overlap_reduced = False
                while overlap and (
                    len(primary) + len(overlap) > resolved_policy.max_units_per_chunk
                    or sum(unit_size(uid) for uid in primary + overlap)
                    > resolved_policy.max_payload_chars
                ):
                    first = overlap[0]
                    group = keep_group_by_unit.get(first)
                    remove_ids = (
                        set(group.unit_ids)
                        if group is not None
                        else {first}
                    )
                    overlap = [uid for uid in overlap if uid not in remove_ids]
                    overlap_reduced = True

                combined = primary + overlap
                shared_context = context_for(combined)
                chunk_identity = {
                    "bundle_id": bundle.bundle_id,
                    "partition_key": partition_key,
                    "unit_ids": primary,
                    "overlap_unit_ids": overlap,
                }
                final_chunks.append(
                    ContentChunk(
                        chunk_id=_stable_id("chunk", chunk_identity),
                        bundle_id=bundle.bundle_id,
                        partition_key=partition_key,
                        unit_ids=primary,
                        overlap_unit_ids=overlap,
                        shared_context=shared_context,
                        estimated_payload_chars=sum(
                            unit_size(uid) for uid in combined
                        ),
                        metadata={
                            "overlap_reduced": overlap_reduced,
                        },
                    )
                )
                history.extend(primary)

        plan_identity = {
            "bundle_id": bundle.bundle_id,
            "strategy_ref": strategy_ref,
            "chunks": [chunk.chunk_id for chunk in final_chunks],
            "policy": resolved_policy.model_dump(mode="json"),
        }
        return ContentPlan(
            plan_id=_stable_id("plan", plan_identity),
            bundle_id=bundle.bundle_id,
            strategy_ref=strategy_ref,
            chunks=final_chunks,
            metadata={
                "policy": resolved_policy.model_dump(mode="json"),
                "logical_unit_count": len(units),
                "chunk_count": len(final_chunks),
            },
        )
