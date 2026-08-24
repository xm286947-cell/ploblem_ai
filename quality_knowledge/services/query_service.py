from __future__ import annotations
from typing import Any


class IssueQueryService:
    """M3 query facade for cross-business issue knowledge and aggregates."""

    def __init__(self, repository):
        self.repository = repository

    def query_issues(self, **filters):
        limit = int(filters.pop("limit", 100))
        return self.repository.query(filters, limit=limit)

    def find_issue_by_business_id(self, business_type, issue_id):
        return self.repository.query({"business_type": business_type, "issue_id": issue_id})

    def list_capability_gaps(self, **filters):
        limit = int(filters.pop("limit", 100))
        return self.repository.query_capability_gaps(filters, limit=limit)

    def aggregate_by_cause(self, *, business_type=None, level="l1", limit=20):
        return self.repository.aggregate("occurrence", level=level, business_type=business_type, limit=limit)

    def aggregate_by_escape(self, *, business_type=None, level="l1", limit=20):
        return self.repository.aggregate("escape", level=level, business_type=business_type, limit=limit)

    def aggregate_by_capability_gap(self, *, business_type=None, dimension=None, limit=20):
        return self.repository.aggregate_capability_gaps(business_type=business_type, dimension=dimension, limit=limit)

    def statistics(self, *, business_type=None, limit=20):
        return self.repository.statistics(business_type=business_type, limit=limit)
