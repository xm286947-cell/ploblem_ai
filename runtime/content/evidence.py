from __future__ import annotations

from runtime.contracts import EvidenceReference, EvidenceLocator, SourceRef
from runtime.content.errors import PartitionMismatchError


class EvidenceIntegrityError(Exception):
    code = "EVIDENCE_INTEGRITY_ERROR"


class EvidenceRegistry:
    def __init__(self):
        self._items: dict[str, EvidenceReference] = {}

    def save(self, evidence: EvidenceReference) -> EvidenceReference:
        existing = self._items.get(evidence.evidence_id)
        if existing is not None:
            if existing.model_dump(mode="json") != evidence.model_dump(mode="json"):
                raise EvidenceIntegrityError(
                    f"evidence_id collision: {evidence.evidence_id}"
                )
            return existing
        missing = [
            item
            for item in evidence.derived_from
            if item not in self._items
        ]
        if missing:
            raise EvidenceIntegrityError(
                f"derived evidence references unknown evidence: {missing}"
            )

        if evidence.derived_from and not evidence.metadata.get("cross_partition"):
            partitions = {
                self._items[parent].partition_key
                for parent in evidence.derived_from
            }
            partitions.add(evidence.partition_key)
            if len(partitions) > 1:
                raise PartitionMismatchError(
                    "derived evidence cannot cross partitions by default",
                    details={
                        "evidence_id": evidence.evidence_id,
                        "partitions": sorted(
                            "__none__" if value is None else value
                            for value in partitions
                        ),
                    },
                )

        self._items[evidence.evidence_id] = evidence
        self._assert_acyclic(evidence.evidence_id)
        return evidence

    def derive(
        self,
        *,
        evidence_id: str,
        source: SourceRef,
        locator: EvidenceLocator,
        derived_from: list[str],
        excerpt: str | None = None,
        confidence: float | None = None,
        partition_key: str | None = None,
        metadata: dict | None = None,
        cross_partition: bool = False,
    ) -> EvidenceReference:
        merged_metadata = dict(metadata or {})
        if cross_partition:
            merged_metadata["cross_partition"] = True
        evidence = EvidenceReference(
            evidence_id=evidence_id,
            source=source,
            locator=locator,
            excerpt=excerpt,
            confidence=confidence,
            derived_from=list(derived_from),
            partition_key=partition_key,
            metadata=merged_metadata,
        )
        return self.save(evidence)

    def get(self, evidence_id: str) -> EvidenceReference:
        try:
            return self._items[evidence_id]
        except KeyError as exc:
            raise EvidenceIntegrityError(
                f"evidence not found: {evidence_id}"
            ) from exc

    def lineage(self, evidence_id: str) -> list[EvidenceReference]:
        ordered: list[EvidenceReference] = []
        seen: set[str] = set()

        def visit(current_id: str) -> None:
            if current_id in seen:
                return
            seen.add(current_id)
            item = self.get(current_id)
            ordered.append(item)
            for parent in item.derived_from:
                visit(parent)

        visit(evidence_id)
        return ordered

    def validate_integrity(self, evidence_ids: list[str] | None = None) -> bool:
        targets = evidence_ids or list(self._items)
        for evidence_id in targets:
            self._assert_acyclic(evidence_id)
            item = self.get(evidence_id)
            if not item.source.fingerprint:
                raise EvidenceIntegrityError(
                    f"evidence source has empty fingerprint: {evidence_id}"
                )
            for parent in item.derived_from:
                self.get(parent)
        return True

    def _assert_acyclic(self, evidence_id: str) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(current_id: str) -> None:
            if current_id in visiting:
                raise EvidenceIntegrityError(
                    f"evidence lineage cycle detected at {current_id}"
                )
            if current_id in visited:
                return
            visiting.add(current_id)
            item = self.get(current_id)
            for parent in item.derived_from:
                visit(parent)
            visiting.remove(current_id)
            visited.add(current_id)

        visit(evidence_id)
