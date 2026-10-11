"""Read-only, strict multi-problem grouping *suggestions* over human-confirmed reviews.

Never calls ScenarioAssets.group(); never creates/merges published assets.
A suggestion is a human decision aid, not AI semantic equivalence.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable
from fastapi import APIRouter, HTTPException, Request

from quality_knowledge.qs_next_review import ReviewStore

CONTRACT_VERSION = "qs-aggregation-suggestion/v1"
FAILURE_KEY_BY_DOMAIN = {
    "SOFTWARE": "software_failure_mode",
    "HARDWARE": "hardware_failure_mode",
    "MECHANICAL": "mechanical_failure_mode",
}


def _normalized(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def suggest_pairs(store: ReviewStore) -> dict[str, Any]:
    """Propose precise 1:1 matches; do not group across products or domains.

    Criteria: two *different problems*, confirmed human reviews, identical
    original product code, lifecycle and activity, and domain-specific failure
    mode. A mismatching explicit quality characteristic is not mergeable.
    No free-text fuzzy similarity or unconfirmed case is accepted.
    """
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    skipped_stale = 0
    with store._db() as db:
        rows = [dict(row) for row in db.execute(
            "SELECT * FROM qs_next_review WHERE state='CONFIRMED' ORDER BY review_id")]
    for row in rows:
        try:
            store._ensure_current(row)
        except Exception:
            skipped_stale += 1
            continue
        record = store._view(row)
        domains=record["problem_domains"]
        if len(domains) != 1 or record["unresolved_conflicts"] or record["required_missing"]:
            continue
        field = FAILURE_KEY_BY_DOMAIN.get(domains[0])
        if not field:
            continue
        values = record["effective_fields"]
        product = _normalized(values.get("product_code",""))
        lifecycle = _normalized(values.get("lifecycle_code",""))
        activity = _normalized(values.get("activity_code",""))
        failure = _normalized(values.get(field,""))
        problem = record["problem_ref_context"]
        if not all((product,lifecycle,activity,failure,problem)):
            continue
        key = (product,domains[0],lifecycle,activity,failure)
        record["matching_failure_field"]=field
        groups[key].append(record)
    pairs=[]
    for key, items in sorted(groups.items()):
        for i, left in enumerate(items):
            for right in items[i+1:]:
                if left["problem_ref_context"] == right["problem_ref_context"]:
                    continue
                ql = left["effective_fields"].get("quality_attribute", "")
                qr = right["effective_fields"].get("quality_attribute", "")
                if ql and qr and _normalized(ql) != _normalized(qr):
                    continue
                links=[]
                for candidate in (left,right):
                    f=candidate["matching_failure_field"]
                    links.append({
                        "review_id":candidate["review_id"],
                        "problem_ref":candidate["problem_ref_context"],
                        "failure_mode":candidate["effective_fields"][f],
                        "origin_evidence":candidate["original_field_evidence"].get(f,[]),
                        "human_revision":candidate["human_edits"].get(f),
                        "source_refs":candidate["source_refs"],
                    })
                pairs.append({
                    "status":"HUMAN_ASSET_REVIEW_REQUIRED",
                    "matching_rule":"SAME_PRODUCT_DOMAIN_LIFECYCLE_ACTIVITY_FAILURE",
                    "shared_product_code":left["effective_fields"]["product_code"],
                    "problem_domain":key[1],
                    "lifecycle_code":left["effective_fields"]["lifecycle_code"],
                    "activity_code":left["effective_fields"]["activity_code"],
                    "failure_field":left["matching_failure_field"],
                    "members":links,
                    "asset_created":False,
                    "auto_grouped":False,
                })
    return {
        "contract_version":CONTRACT_VERSION,
        "status":"SUGGESTION_ONLY",
        "suggested_pairs":pairs,
        "pair_count":len(pairs),
        "skipped_stale_review_count":skipped_stale,
        "assets_changed":False,
    }


def create_suggestion_router(
    store: ReviewStore, *,
    trusted_actor: Callable[[Request],str] | None = None,
    authorize: Callable[[str,str,str],bool] | None = None,
) -> APIRouter:
    router=APIRouter(tags=["QS Next Aggregation Suggestions"])

    @router.get("/api/v2/qs-next/aggregation/v1/suggestions")
    def suggestions(request: Request):
        if trusted_actor is None or authorize is None:
            raise HTTPException(403,detail="TRUSTED_SECURITY_BINDING_REQUIRED")
        actor=trusted_actor(request)
        if not actor or not authorize(actor,"AGGREGATION_READ","QUALITY_SCENARIOS"):
            raise HTTPException(403,detail="AGGREGATION_PERMISSION_DENIED")
        return suggest_pairs(store)

    return router


__all__=["CONTRACT_VERSION","suggest_pairs","create_suggestion_router"]
