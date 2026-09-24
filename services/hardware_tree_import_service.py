"""Application service boundary for HC-TREE-IMPORT-001.

M1 exposes import lifecycle and version governance without knowing Excel parsing
details. HC-TREE-M2 will feed parsed profiles, validation issues and ChangeSets
through this boundary.
"""
from __future__ import annotations

from typing import Any

from repositories.hardware_tree_import_repository import HardwareTreeImportRepository


class HardwareTreeImportService:
    def __init__(self, repository: HardwareTreeImportRepository):
        self.repository = repository

    def create_import(self, **payload: Any) -> dict[str, Any]:
        return self.repository.create_job(**payload)

    def get_import(self, job_id: str) -> dict[str, Any]:
        return self.repository.get_job(job_id)

    def list_imports(self, tree_type: str | None = None) -> list[dict[str, Any]]:
        return self.repository.list_jobs(tree_type)

    def set_mapping_profile(
        self, job_id: str, profile: dict[str, Any]
    ) -> dict[str, Any]:
        return self.repository.set_mapping_profile(job_id, profile)

    def advance(self, job_id: str, status: str) -> dict[str, Any]:
        return self.repository.advance_status(job_id, status)

    def add_validation_issue(
        self, job_id: str, issue: dict[str, Any]
    ) -> dict[str, Any]:
        return self.repository.add_issue(job_id, issue)

    def resolve_validation_issue(self, issue_id: str) -> dict[str, Any]:
        return self.repository.resolve_issue(issue_id)

    def save_change(
        self, job_id: str, change: dict[str, Any]
    ) -> dict[str, Any]:
        return self.repository.save_change(job_id, change)

    def decide_change(
        self,
        change_id: str,
        decision: str,
        *,
        resolved_after: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.repository.set_change_decision(
            change_id, decision, resolved_after=resolved_after
        )

    def ready_to_apply(self, job_id: str) -> dict[str, Any]:
        return self.repository.mark_ready_to_apply(job_id)

    def apply(self, job_id: str) -> dict[str, Any]:
        return self.repository.apply_job(job_id)

    def active_version(self, tree_type: str) -> dict[str, Any] | None:
        return self.repository.get_active_version(tree_type)

    def version_nodes(self, version_id: str) -> list[dict[str, Any]]:
        return self.repository.list_version_nodes(version_id)
