from pathlib import Path

from fastapi.testclient import TestClient

from storage_life import core
from storage_life import knowledge as knowledge_store
from storage_life.app import app


def test_verified_knowledge_api_and_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "knowledge.sqlite3")
    client = TestClient(app)
    text = "# Synthetic storage note\nWear leveling distributes writes across NAND blocks.\n寿命监测应保留原文证据。"
    response = client.post("/api/v1/knowledge/sources",
        data={"title": "Synthetic note", "publisher": "Test publisher", "official_url": "https://example.invalid/note", "version": "v1"},
        files={"file": ("synthetic.md", text.encode(), "text/markdown")})
    assert response.status_code == 201, response.text
    source_id = response.json()["source_id"]
    assert response.json()["verify_status"] == "indexed"
    assert response.json()["passage_count"] == 1
    assert client.get("/api/v1/knowledge/search", params={"q": "Wear leveling"}).json()["status"] == "no_evidence"
    detail = client.get(f"/api/v1/knowledge/sources/{source_id}").json()
    assert detail["sha256"] and "local_path" not in detail
    assert detail["passages"][0]["page_number"] == 1
    assert client.patch(f"/api/v1/knowledge/sources/{source_id}/review",
        json={"status": "verified", "verified_by": "test reviewer"}).status_code == 200
    result = client.get("/api/v1/knowledge/search", params={"q": "Wear leveling"}).json()
    assert result["status"] == "evidenced"
    hit = result["items"][0]
    assert hit["kind"] == "verified_knowledge"
    assert hit["evidence"]["source_id"] == source_id
    assert hit["evidence"]["page"] == 1
    assert hit["evidence"]["official_url"] == "https://example.invalid/note"
    passage = client.get(f"/api/v1/knowledge/passages/{hit['evidence']['passage_id']}").json()
    assert passage["source_id"] == source_id and passage["page_number"] == 1
    assert client.get("/api/v1/knowledge/search", params={"q": "寿命监测"}).json()["status"] == "evidenced"
    with knowledge_store.connect() as con:
        con.execute("DROP TABLE IF EXISTS knowledge_fts")
    # Simulate an older SQLite build without FTS5 support.
    monkeypatch.setattr(knowledge_store, "initialize", lambda con: None)
    assert client.get("/api/v1/knowledge/search", params={"q": "寿命监测"}).json()["status"] == "evidenced"
    assert client.get(f"/api/v1/knowledge/sources/{source_id}/file").status_code == 200
    assert client.patch(f"/api/v1/knowledge/sources/{source_id}/review",
        json={"status": "rejected", "verified_by": "test reviewer"}).status_code == 200
    assert client.get("/api/v1/knowledge/search", params={"q": "Wear leveling"}).json()["status"] == "no_evidence"


def test_pdf_passage_keeps_page_and_confirmed_spec_appears(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_LIFE_ENABLE_RULE_FALLBACK", "1")
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "knowledge.sqlite3")
    client = TestClient(app)
    fixture = Path(__file__).parents[1] / "examples" / "synthetic_ssd.pdf"
    with fixture.open("rb") as stream:
        response = client.post("/api/v1/knowledge/sources", data={"title": "SSD note", "publisher": "Synthetic"},
            files={"file": (fixture.name, stream, "application/pdf")})
    assert response.status_code == 201, response.text
    sid = response.json()["source_id"]
    client.patch(f"/api/v1/knowledge/sources/{sid}/review", json={"status": "verified", "verified_by": "reviewer"})
    result = client.get("/api/v1/knowledge/search", params={"q": "TBW"}).json()
    assert result["items"][0]["evidence"]["page"] == 1
    assert result["items"][0]["evidence"]["local_url"].endswith("#page=1")
    with fixture.open("rb") as stream:
        imported = client.post("/api/documents", data={"vendor": "Synthetic", "model": "SSD-1", "device_type": "SSD"},
            files={"file": (fixture.name, stream, "application/pdf")}).json()
    candidates = client.get(f"/api/devices/{imported['device_id']}/candidates").json()
    tbw = next(c for c in candidates if c["canonical_name"] == "tbw")
    client.patch(f"/api/candidates/{tbw['id']}", json={"status": "confirmed", "verified_by": "reviewer"})
    result = client.get("/api/v1/knowledge/search", params={"q": "TBW"}).json()
    assert {x["kind"] for x in result["items"]} == {"verified_knowledge", "confirmed_specification"}


def test_invalid_knowledge_source_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "knowledge.sqlite3")
    client = TestClient(app)
    response = client.post("/api/v1/knowledge/sources", data={"title": "x", "publisher": "y"},
        files={"file": ("bad.pdf", b"not pdf", "application/pdf")})
    assert response.status_code == 422
    assert client.get("/api/v1/knowledge/sources").json() == []
