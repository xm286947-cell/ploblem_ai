"""Ensure an early route failure cannot be hidden by a later successful route."""
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from scripts import overall_vnext_demo as demo
from quality_knowledge.web.app import create_legacy_quality_issue_router


@pytest.mark.parametrize("failed_path", ["/p0/overall", "/analysis"])
def test_demo_check_rejects_nonfinal_route_failure(tmp_path, monkeypatch, failed_path):
    original_get = TestClient.get

    def get(client, url, *args, **kwargs):
        if url == failed_path:
            return httpx.Response(503)
        return original_get(client, url, *args, **kwargs)

    monkeypatch.setattr(TestClient, "get", get)
    monkeypatch.setattr(sys, "argv", ["demo", "--check", "--data-dir", str(tmp_path)])
    with pytest.raises(RuntimeError, match=f"MVP_ROUTE_FAILED:{failed_path}:503"):
        demo.main()


def test_legacy_seed_ignores_stale_fixed_name_workbook(tmp_path):
    stale_workbook = tmp_path / "legacy_demo_seed.xlsx"
    stale_workbook.write_bytes(b"stale workbook held by another Windows process")

    legacy_db = demo.prepare_demo_legacy_database(tmp_path)
    _, state = create_legacy_quality_issue_router(
        legacy_db,
        initialize_schema=False,
    )

    issues = state.knowledge_issue_service.query_issues(
        {"business_issue_id": demo.DEMO_LEGACY_ISSUE_ID},
        limit=1,
    )
    assert issues
    assert stale_workbook.read_bytes() == b"stale workbook held by another Windows process"
    assert not list(tmp_path.glob("legacy_demo_seed_*.xlsx"))


def test_demo_startup_continues_when_windows_locks_temporary_seed_workbook(tmp_path, monkeypatch):
    original_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name.startswith("legacy_demo_seed_"):
            raise PermissionError("synthetic Windows file lock")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr("pathlib.Path.unlink", unlink)
    legacy_db = demo.prepare_demo_legacy_database(tmp_path)

    _, state = create_legacy_quality_issue_router(legacy_db, initialize_schema=False)
    issues = state.knowledge_issue_service.query_issues(
        {"business_issue_id": demo.DEMO_LEGACY_ISSUE_ID}, limit=1
    )
    assert issues
    assert legacy_db.exists()
    assert len(list(tmp_path.glob("legacy_demo_seed_*.xlsx"))) == 1
