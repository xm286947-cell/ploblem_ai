from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from storage_life import core, knowledge
from storage_life.app import app


def test_url_download_cache_review_and_reuse(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "knowledge.sqlite3")
    monkeypatch.setenv("STORAGE_LIFE_SOURCE_HOSTS", "docs.example.org")
    pdf = (Path(__file__).parents[1] / "examples" / "synthetic_ssd.pdf").read_bytes()
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=pdf)

    remote = httpx.Client(transport=httpx.MockTransport(handler))
    real_fetch = knowledge.fetch_official_source
    monkeypatch.setattr(knowledge, "fetch_official_source",
                        lambda *args: real_fetch(*args, client=remote))
    client = TestClient(app)
    body = {"official_url": "https://docs.example.org/spec.pdf", "title": "Synthetic SSD",
            "publisher": "Example publisher", "version": "v1"}
    first = client.post("/api/v1/knowledge/sources/from-url", json=body)
    assert first.status_code == 201, first.text
    assert first.json()["reused"] is False
    source_id = first.json()["source_id"]
    detail = client.get(f"/api/v1/knowledge/sources/{source_id}").json()
    assert detail["sha256"] and detail["official_url"] == body["official_url"]
    assert detail["page_count"] == 1 and detail["passages"][0]["page_number"] == 1
    assert client.get("/api/v1/knowledge/search", params={"q": "TBW"}).json()["status"] == "no_evidence"
    client.patch(f"/api/v1/knowledge/sources/{source_id}/review",
                 json={"status": "verified", "verified_by": "reviewer"})
    second = client.post("/api/v1/knowledge/sources/from-url", json=body)
    assert second.status_code == 201
    assert second.json()["reused"] is True
    assert second.json()["source_id"] == source_id and second.json()["verify_status"] == "verified"
    assert len(client.get("/api/v1/knowledge/sources").json()) == 1
    assert len(calls) == 2
    assert client.get("/api/v1/knowledge/search", params={"q": "TBW"}).json()["status"] == "evidenced"
    remote.close()


def test_url_ingest_rejects_unlisted_and_redirect(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "knowledge.sqlite3")
    monkeypatch.setenv("STORAGE_LIFE_SOURCE_HOSTS", "docs.example.org")
    client = TestClient(app)
    body = {"official_url": "https://private.example.org/spec.pdf", "title": "x", "publisher": "y"}
    assert client.post("/api/v1/knowledge/sources/from-url", json=body).status_code == 422
    redirect = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(302, headers={"location": "https://other.example.org/"})))
    real_fetch = knowledge.fetch_official_source
    monkeypatch.setattr(knowledge, "fetch_official_source", lambda *args: real_fetch(*args, client=redirect))
    body["official_url"] = "https://docs.example.org/spec.pdf"
    assert client.post("/api/v1/knowledge/sources/from-url", json=body).status_code == 502
    assert client.get("/api/v1/knowledge/sources").json() == []
    redirect.close()
    oversize = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "application/pdf",
            "content-length": str(knowledge.MAX_SOURCE_BYTES + 1)}, content=b"x")))
    monkeypatch.setattr(knowledge, "fetch_official_source", lambda *args: real_fetch(*args, client=oversize))
    assert client.post("/api/v1/knowledge/sources/from-url", json=body).status_code == 422
    assert client.get("/api/v1/knowledge/sources").json() == []
    oversize.close()
