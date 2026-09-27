"""Ensure an early route failure cannot be hidden by a later successful route."""
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from scripts import overall_vnext_demo as demo


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
