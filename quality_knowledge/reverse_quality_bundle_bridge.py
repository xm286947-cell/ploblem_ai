"""W2 production entry: frozen Bundle -> mature Reverse Quality service/runtime."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from quality_knowledge.reverse_quality_bundle_adapter import ReverseQualityInputAdapter


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _same_bundle_lineage(previous: dict[str, Any], provenance: dict[str, Any]) -> bool | None:
    """Return True/False for known Bundle lineage, None when legacy provenance is unknown."""
    previous_provenance = previous.get("bundle_provenance")
    if not isinstance(previous_provenance, dict):
        previous_input = previous.get("input")
        if isinstance(previous_input, dict):
            previous_provenance = previous_input.get("bundle_provenance")
    if not isinstance(previous_provenance, dict):
        return None
    return (
        str(previous_provenance.get("bundle_id") or "") == str(provenance.get("bundle_id") or "")
        and str(previous_provenance.get("bundle_revision") or "") == str(provenance.get("bundle_revision") or "")
    )


class ReverseQualityBundleBridge:
    """Use mature analysis semantics without invoking its legacy source lookup."""

    def __init__(self, reverse_quality_service, bundle_snapshot_store):
        self.service = reverse_quality_service
        self.bundle_snapshot_store = bundle_snapshot_store
        self.input_adapter = ReverseQualityInputAdapter()

    def analyse(self, bundle: dict[str, Any], *, taxonomy: dict[str, Any] | None = None):
        if getattr(self.service, "ai_client", None) is not None:
            raise ValueError("REVERSE_QUALITY_UNIFIED_RUNTIME_REQUIRED")
        facts = self.input_adapter.adapt(bundle)
        selected = facts["bundle_provenance"]["selected_issue"]
        frozen = self.bundle_snapshot_store.get(
            facts["bundle_provenance"]["bundle_id"],
            facts["bundle_provenance"]["bundle_revision"],
        )
        if frozen is None:
            raise ValueError("SCENARIO_SOURCE_BUNDLE_SNAPSHOT_NOT_FOUND")
        if frozen != bundle:
            raise ValueError("SCENARIO_SOURCE_BUNDLE_SNAPSHOT_MISMATCH")
        product_code = str(selected.get("product_code") or "").strip()
        if not product_code:
            raise ValueError("BUNDLE_PRODUCT_CODE_REQUIRED")
        if taxonomy is None:
            taxonomy = self.service.scenarios.taxonomy_active(product_code)
        if not taxonomy:
            raise ValueError("REVERSE_QUALITY_TAXONOMY_NOT_FOUND")

        canonical = facts["canonical_itr"]
        provenance = facts["bundle_provenance"]
        source_hash = hashlib.sha256(_json({
            "bundle_id": provenance["bundle_id"],
            "bundle_revision": provenance["bundle_revision"],
            "facts": facts,
            "taxonomy_version_id": taxonomy.get("version_id"),
        }).encode("utf-8")).hexdigest()
        previous = self.service.get(canonical)
        if previous and previous.get("source_hash") == source_hash:
            return previous

        # Human review protects the exact frozen Bundle lineage. A newer Bundle
        # revision is a new production lineage and may be analysed without
        # mutating the reviewed run. Legacy results without Bundle provenance
        # remain protected because a distinct lineage cannot be proven.
        lineage_match = _same_bundle_lineage(previous, provenance) if previous else False
        review_protected = previous is not None and lineage_match is not False
        if review_protected and previous.get("status") == "CONFIRMED":
            raise ValueError("已有人工确认记录，不能由同一 Bundle 修订分析覆盖")
        if review_protected and any(
            item.get("review_status") in {"CONFIRMED", "REJECTED"}
            for item in (previous.get("review") or {}).values()
        ):
            raise ValueError("同一 Bundle 修订已有人工逐字段审核；不允许覆盖")
        if review_protected and previous.get("match_reviewed"):
            raise ValueError("同一 Bundle 修订的场景匹配已人工审核；不允许覆盖")

        run = self.service.repository.start_run(
            canonical_itr=canonical,
            product_code=product_code,
            taxonomy_version_id=str(taxonomy.get("version_id") or ""),
            source_hash=source_hash,
            input_payload=facts,
        )
        try:
            # _analyse_run owns the mature field/evidence validation and calls
            # ReverseQualityRuntimeExecutor. This path never calls facts(id).
            return self.service._analyse_run(
                facts, taxonomy, source_hash, run["run_id"], product_code
            )
        except Exception as exc:
            self.service.repository.fail_run(run["run_id"], str(exc))
            raise


__all__ = ["ReverseQualityBundleBridge"]
