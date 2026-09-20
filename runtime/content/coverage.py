from __future__ import annotations

from typing import Iterable

from runtime.contracts import Coverage, CoverageUniverse, Range


def _normalize_ranges(ranges: Iterable[Range]) -> list[Range]:
    ordered = sorted(
        (
            Range(start=item.start, end=item.end, locator=item.locator)
            for item in ranges
            if item.end > item.start
        ),
        key=lambda item: (item.start, item.end),
    )
    merged: list[Range] = []
    for item in ordered:
        if not merged or item.start > merged[-1].end:
            merged.append(item)
            continue
        previous = merged[-1]
        merged[-1] = Range(
            start=previous.start,
            end=max(previous.end, item.end),
            locator=previous.locator,
        )
    return merged


def _intersection(left: list[Range], right: list[Range]) -> list[Range]:
    overlaps: list[Range] = []
    for a in _normalize_ranges(left):
        for b in _normalize_ranges(right):
            start = max(a.start, b.start)
            end = min(a.end, b.end)
            if end > start:
                overlaps.append(Range(start=start, end=end))
    return _normalize_ranges(overlaps)


def _subtract(base: list[Range], covered: list[Range]) -> list[Range]:
    result: list[Range] = []
    covered_norm = _normalize_ranges(covered)
    for target in _normalize_ranges(base):
        cursor = target.start
        for item in covered_norm:
            if item.end <= cursor:
                continue
            if item.start >= target.end:
                break
            if item.start > cursor:
                result.append(
                    Range(start=cursor, end=min(item.start, target.end))
                )
            cursor = max(cursor, item.end)
            if cursor >= target.end:
                break
        if cursor < target.end:
            result.append(Range(start=cursor, end=target.end))
    return _normalize_ranges(result)


def _length(ranges: list[Range]) -> float:
    return float(sum(item.end - item.start for item in _normalize_ranges(ranges)))


class CoverageCalculator:
    def calculate(
        self,
        universe: CoverageUniverse,
        *,
        processed_unit_ids: Iterable[str] = (),
        failed_unit_ids: Iterable[str] = (),
        processed_ranges: Iterable[Range] = (),
        failed_ranges: Iterable[Range] = (),
    ) -> Coverage:
        if universe.coverage_type == "RANGE":
            targets = _normalize_ranges(universe.range_targets)
            processed = _intersection(list(processed_ranges), targets)
            failed = _intersection(list(failed_ranges), targets)
            known = _normalize_ranges(processed + failed)
            pending = _subtract(targets, known)
            total = _length(targets)
            processed_length = _length(processed)
            ratio = 1.0 if total == 0 else processed_length / total
            complete = ratio >= 1.0 and not failed and not pending
            return Coverage(
                source=universe.source,
                universe_fingerprint=universe.universe_fingerprint,
                coverage_type=universe.coverage_type,
                required_units=[],
                processed_units=[],
                failed_units=[],
                pending_units=[],
                processed_ranges=processed,
                failed_ranges=failed,
                pending_ranges=pending,
                coverage_ratio=ratio,
                complete=complete,
                partition_key=universe.partition_key,
            )

        target_ids = {item.unit_id for item in universe.unit_targets}
        required_ids = {
            item.unit_id
            for item in universe.unit_targets
            if item.required
        }
        processed = set(processed_unit_ids).intersection(target_ids)
        failed = set(failed_unit_ids).intersection(target_ids) - processed
        pending = required_ids - processed - failed

        required_processed = required_ids.intersection(processed)
        ratio = (
            1.0
            if not required_ids
            else len(required_processed) / len(required_ids)
        )
        complete = ratio >= 1.0 and not required_ids.intersection(failed | pending)
        return Coverage(
            source=universe.source,
            universe_fingerprint=universe.universe_fingerprint,
            coverage_type=universe.coverage_type,
            required_units=sorted(required_ids),
            processed_units=sorted(processed),
            failed_units=sorted(failed),
            pending_units=sorted(pending),
            processed_ranges=[],
            failed_ranges=[],
            pending_ranges=[],
            coverage_ratio=ratio,
            complete=complete,
            partition_key=universe.partition_key,
        )
