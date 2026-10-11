"""Read-only controlled PATCH57 taxonomy gate for QS Next human confirmation.

Never initializes or modifies original dictionary tables. A domain/product
authorization grant MUST come from the trusted deployment configuration.
The original taxonomy defines product/stage/activity but does not itself
certify whether a given domain is enabled for this new workflow.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any, Mapping, AbstractSet

from quality_knowledge.qs_next_gateway import read_only_db

DOMAINS = frozenset({"SOFTWARE", "HARDWARE", "MECHANICAL"})


class OriginalTaxonomyGate:
    def __init__(
        self,
        original_db: str | Path,
        *,
        approved_domain_scopes: Mapping[str, AbstractSet[str]] | None = None,
    ):
        self.original_db = Path(original_db).resolve()
        scopes = approved_domain_scopes or {}
        self.scopes: dict[str, frozenset[str]] = {}
        for product, domains in scopes.items():
            if (not isinstance(product, str) or not product.strip()
                    or not isinstance(domains, (set, frozenset, tuple, list))):
                raise ValueError("INVALID_TAXONOMY_DOMAIN_GRANT")
            approved = frozenset(domains)
            if not approved or not approved.issubset(DOMAINS):
                raise ValueError("INVALID_TAXONOMY_DOMAIN_GRANT")
            self.scopes[product.strip()] = approved

    def verify(self, review: Mapping[str, Any]) -> dict[str, Any]:
        """Return evidence of a positive match, without inferring missing data."""
        fields = review.get("effective_fields")
        domains = review.get("problem_domains")
        if not isinstance(fields, Mapping) or not isinstance(domains, (list, tuple)):
            return {"verified": False, "code": "INVALID_REVIEW_CONTRACT"}
        product = fields.get("product_code")
        lifecycle = fields.get("lifecycle_code")
        activity = fields.get("activity_code")
        if not all(isinstance(v, str) and v.strip() for v in (product, lifecycle, activity)):
            return {"verified": False, "code": "TAXONOMY_FIELDS_REQUIRED"}
        if (not domains or len(domains) != len(set(domains))
                or not set(domains).issubset(DOMAINS)):
            return {"verified": False, "code": "INVALID_PROBLEM_DOMAINS"}
        if not set(domains).issubset(self.scopes.get(product, frozenset())):
            return {"verified": False, "code": "DOMAIN_TAXONOMY_SCOPE_NOT_APPROVED"}
        try:
            with read_only_db(self.original_db) as db:
                versions = db.execute(
                    """SELECT version_id, version_no FROM scenario_taxonomy_version
                       WHERE product_code=? AND status='ACTIVE'""",
                    (product,),
                ).fetchall()
                if len(versions) != 1:
                    return {"verified": False, "code": "ACTIVE_TAXONOMY_AMBIGUOUS_OR_MISSING"}
                version_id = versions[0]["version_id"]
                lifecycle_row = db.execute(
                    """SELECT lifecycle_code FROM scenario_lifecycle
                       WHERE version_id=? AND lifecycle_code=? AND enabled=1""",
                    (version_id, lifecycle),
                ).fetchone()
                if lifecycle_row is None:
                    return {"verified": False, "code": "LIFECYCLE_NOT_APPROVED"}
                activity_row = db.execute(
                    """SELECT activity_code FROM scenario_activity
                       WHERE version_id=? AND activity_code=?
                         AND lifecycle_code=? AND enabled=1""",
                    (version_id, activity, lifecycle),
                ).fetchone()
                if activity_row is None:
                    return {"verified": False, "code": "ACTIVITY_NOT_APPROVED_FOR_LIFECYCLE"}
                return {
                    "verified": True,
                    "code": "ACTIVE_TAXONOMY_MATCH",
                    "product_code": product,
                    "taxonomy_version_id": version_id,
                    "taxonomy_version_no": versions[0]["version_no"],
                    "lifecycle_code": lifecycle,
                    "activity_code": activity,
                    "approved_domains": sorted(domains),
                }
        except (sqlite3.Error, OSError, ValueError):
            return {"verified": False, "code": "ORIGINAL_TAXONOMY_DATA_UNAVAILABLE"}

    def __call__(self, review: Mapping[str, Any]) -> bool:
        return self.verify(review)["verified"] is True


__all__ = ["OriginalTaxonomyGate", "DOMAINS"]
