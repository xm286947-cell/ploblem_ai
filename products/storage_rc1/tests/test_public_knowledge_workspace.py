from fastapi.testclient import TestClient

from storage_life import public_knowledge
from storage_life.app import app


client = TestClient(app)


def test_fixture_replay_supports_sources_search_detail_and_citation():
    assert client.get("/api/public-knowledge/status").json()["mode"] == "FIXTURE_REPLAY"
    sources = client.get("/api/public-knowledge/sources").json()["sources"]
    assert sources and all(x["classification"] == "PUBLIC" for x in sources)
    hit = client.post("/api/public-knowledge/search", json={"query": "GD25Q64E page size"}).json()["hits"][0]
    assert hit["source_id"] == "fixture-gd25q64e"
    assert client.get("/api/public-knowledge/sources/fixture-gd25q64e").status_code == 200
    citation = client.get("/api/public-knowledge/citations/fixture-citation-page1").json()
    assert citation["text"] and citation["locator"]["page"] == 1


def test_fixture_qa_is_explicitly_synthetic_and_cited():
    result = client.post("/api/public-knowledge/ask", json={"question": "GD25Q64E page size?"}).json()
    assert result["answer_scope"] == "SYNTHETIC_DEMO_ONLY"
    assert result["citations"][0]["citation_id"] == "fixture-citation-page1"


def test_fixture_health_does_not_call_model_provider():
    result = client.get("/api/public-knowledge/provider-health").json()
    assert result["status"] == "not_called"


def test_live_status_exposes_only_model_identity_and_safe_status(monkeypatch):
    def request(mode, path, payload=None, base_url=None):
        if path == "/health":
            return {"status": "ok", "service": "public-knowledge", "version": "0.1"}
        if path == "/config":
            return {"config": {
                "ollama_model": "qwen-text:latest",
                "credential_status": {"secret": "must not escape"},
                "parser_status": "ready",
                "api_key": "secret-value",
            }, "config_hash": "safe-hash"}
        raise AssertionError(path)

    monkeypatch.setattr(public_knowledge, "_request", request)
    result = client.get("/api/public-knowledge/status?mode=LIVE").json()
    assert result["model_name"] == "qwen-text:latest"
    assert result["credential_status"] == "not_reported"
    assert result["parser_status"] == "ready"
    assert "secret" not in str(result)
    assert "api_key" not in result


def test_import_rejects_non_public_before_forwarding(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("non-public content must not reach the service")

    monkeypatch.setattr(public_knowledge, "_request", forbidden)
    response = client.post("/api/public-knowledge/sources/import?mode=LIVE", json={
        "title": "private", "content": "secret", "classification": "INTERNAL"
    })
    assert response.status_code == 422


def test_file_import_rejects_non_public_before_forwarding(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("non-public upload must not reach the service")

    monkeypatch.setattr(public_knowledge, "_request_file", forbidden)
    response = client.post("/api/public-knowledge/sources/import-file?mode=LIVE", data={
        "title": "private", "classification": "INTERNAL"
    }, files={"file": ("private.pdf", b"private bytes", "application/pdf")})
    assert response.status_code == 422


def test_file_import_forwards_original_bytes_as_multipart_only_in_live(monkeypatch):
    seen = {}

    def capture(mode, path, fields, filename, content, media_type, base_url=None):
        seen.update(mode=mode, path=path, fields=fields, filename=filename, content=content, media_type=media_type)
        return {"source_id": "s1", "source_sha256": "sha"}

    monkeypatch.setattr(public_knowledge, "_request_file", capture)
    payload = b"%PDF original"
    response = client.post("/api/public-knowledge/sources/import-file?mode=LIVE", data={
        "title": "public datasheet", "classification": "PUBLIC", "source_uri": "https://vendor.example/doc.pdf"
    }, files={"file": ("doc.pdf", payload, "application/pdf")})
    assert response.status_code == 200
    assert seen == {"mode": "LIVE", "path": "/sources/import-file",
                    "fields": {"title": "public datasheet", "classification": "PUBLIC",
                               "source_uri": "https://vendor.example/doc.pdf"},
                    "filename": "doc.pdf", "content": payload, "media_type": "application/pdf"}


def test_fixture_file_import_stays_read_only():
    response = client.post("/api/public-knowledge/sources/import-file", data={
        "title": "demo", "classification": "PUBLIC"
    }, files={"file": ("doc.pdf", b"%PDF", "application/pdf")})
    assert response.status_code == 409
    assert "只读" in response.text


def test_live_search_does_not_require_qa_provider(monkeypatch):
    monkeypatch.setattr(public_knowledge, "_request", lambda mode, path, payload=None, base_url=None: {
        "hits": [{"hit_id": "h1", "source_id": "s1", "source_revision": "r1", "locator": "page 1", "text": "match", "score": 1.0}],
        "retrieval_snapshot": {"adapter": "sqlite-lexical-reference"},
    })
    response = client.post("/api/public-knowledge/search?mode=LIVE", json={"query": "PRE_EOL_INFO"})
    assert response.status_code == 200
    assert response.json()["hits"][0]["text"] == "match"


def test_live_qa_failure_is_passed_as_fail_closed(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(public_knowledge, "_request", lambda *args, **kwargs: (_ for _ in ()).throw(HTTPException(503, "model unavailable")))
    response = client.post("/api/public-knowledge/ask?mode=LIVE", json={"question": "question"})
    assert response.status_code == 503
    assert "model unavailable" in response.text


def test_generation_request_uses_longer_bounded_timeout(monkeypatch):
    assert public_knowledge._request_timeout_seconds("/search") == 8.0
    assert public_knowledge._request_timeout_seconds("/health") == 8.0
    assert public_knowledge._request_timeout_seconds("/ask") == 60.0

    monkeypatch.setenv("PUBLIC_KNOWLEDGE_GENERATION_TIMEOUT_SECONDS", "120")
    assert public_knowledge._request_timeout_seconds("/ask") == 120.0

    monkeypatch.setenv("PUBLIC_KNOWLEDGE_GENERATION_TIMEOUT_SECONDS", "999")
    assert public_knowledge._request_timeout_seconds("/ask") == 180.0

    monkeypatch.setenv("PUBLIC_KNOWLEDGE_GENERATION_TIMEOUT_SECONDS", "invalid")
    assert public_knowledge._request_timeout_seconds("/ask") == 60.0


def test_model_scan_uses_frozen_storage_model_and_source_filter(monkeypatch):
    calls = []

    def request(mode, path, payload=None, base_url=None):
        calls.append((mode, path, payload))
        if path == "/sources/source-1":
            return {
                "source": {
                    "source_id": "source-1",
                    "title": "NAND endurance datasheet",
                    "classification": "PUBLIC",
                    "revision": "Rev1",
                }
            }
        if path == "/search":
            assert payload["filters"] == {"source_id": "source-1"}
            if "P/E Cycle" in payload["query"]:
                return {
                    "hits": [
                        {
                            "hit_id": "citation-pe",
                            "source_id": "source-1",
                            "source_revision": "Rev1",
                            "locator": {
                                "page": 10,
                                "section": "Reliability",
                            },
                            "text": "Endurance: 100000 P/E cycles.",
                            "score": 1.0,
                        }
                    ]
                }
            return {"hits": []}
        raise AssertionError(path)

    monkeypatch.setattr(public_knowledge, "_request", request)
    response = client.post(
        "/api/public-knowledge/model-scan?mode=LIVE",
        json={
            "source_id": "source-1",
            "device_type": "Raw NAND",
            "top_k_per_parameter": 2,
            "parameter_names": ["pe_cycles", "bit_flip_threshold"],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["device_type"] == "NAND Flash"
    assert payload["primary_focus"] is True
    assert payload["coverage"] == {
        "parameter_count": 2,
        "found_count": 1,
        "not_found_count": 1,
        "role_gap_count": 1,
    }
    parameters = {
        item["canonical_name"]: item for item in payload["parameters"]
    }
    assert parameters["pe_cycles"]["coverage_status"] == "FOUND"
    assert "CALCULATION_RULE" in parameters["pe_cycles"][
        "knowledge_requirements"
    ]
    assert parameters["bit_flip_threshold"]["coverage_status"] == "NOT_FOUND"
    assert (
        parameters["bit_flip_threshold"]["knowledge_gap"]
        == "PARAMETER_ROLE_UNCLASSIFIED"
    )
    assert payload["formal_candidate_eligible"] is False
    assert payload["boundary"]["rag_is_formal_knowledge"] is False
    assert any(path == "/search" for _, path, _ in calls)


def test_model_scan_rejects_unknown_parameter_before_search(monkeypatch):
    def request(mode, path, payload=None, base_url=None):
        if path == "/sources/source-1":
            return {
                "source": {
                    "source_id": "source-1",
                    "title": "NAND datasheet",
                    "classification": "PUBLIC",
                }
            }
        raise AssertionError("search must not run for an unknown model parameter")

    monkeypatch.setattr(public_knowledge, "_request", request)
    response = client.post(
        "/api/public-knowledge/model-scan?mode=LIVE",
        json={
            "source_id": "source-1",
            "device_type": "NAND Flash",
            "parameter_names": ["invented_parameter"],
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MODEL_SCAN_PARAMETER_UNKNOWN"


def test_model_extract_bridges_exact_public_pdf_into_existing_kp(monkeypatch):
    from storage_life import knowledge_product

    scan = {
        "model_version": "storage-lifetime-knowledge/v1",
        "device_type": "NAND Flash",
        "coverage": {
            "parameter_count": 2,
            "found_count": 2,
            "not_found_count": 0,
            "role_gap_count": 0,
        },
        "parameters": [
            {
                "canonical_name": "pe_cycles",
                "coverage_status": "FOUND",
                "queries_tried": ["P/E Cycle"],
                "knowledge_requirements": [
                    "PARAMETER_DEFINITION",
                    "CALCULATION_RULE",
                    "TEST_RULE",
                ],
                "scenario_consumers": ["S1", "S2", "S3", "S4", "S5"],
            },
            {
                "canonical_name": "retention",
                "coverage_status": "FOUND",
                "queries_tried": ["Data Retention"],
                "knowledge_requirements": [
                    "PARAMETER_DEFINITION",
                    "APPLICABILITY_RULE",
                    "TEST_RULE",
                ],
                "scenario_consumers": ["S1", "S2", "S3", "S4", "S5"],
            },
        ],
    }
    monkeypatch.setattr(
        public_knowledge,
        "_model_scan_source",
        lambda body, mode, base_url: scan,
    )
    monkeypatch.setattr(
        public_knowledge,
        "_public_source_detail",
        lambda source_id, mode, base_url: (
            {
                "source_id": source_id,
                "title": "KIOXIA NAND endurance",
                "source_class": "PUBLIC",
                "source_uri": "https://example.com/kioxia.pdf",
            },
            [
                {
                    "revision_id": "rev-1",
                    "original_filename": "kioxia.pdf",
                    "media_type": "application/pdf",
                }
            ],
        ),
    )
    monkeypatch.setattr(
        public_knowledge,
        "source_snapshot",
        lambda source_id, revision_id, mode, base_url: public_knowledge.Response(
            content=b"%PDF exact public snapshot",
            media_type="application/pdf",
        ),
    )

    seen = {}

    def ingest_source(payload, **kwargs):
        seen["ingest_payload"] = payload
        seen["ingest_kwargs"] = kwargs
        return {
            "source_document": {
                "source_id": "PKR-source-1",
                "source_version": "rev-1",
            }
        }

    def extract_source(source_id, source_version, **kwargs):
        seen["extract"] = {
            "source_id": source_id,
            "source_version": source_version,
            **kwargs,
        }
        return {
            "source_id": source_id,
            "source_version": source_version,
            "candidate_count": 2,
            "candidate_ids": ["KPC-1", "KPC-2"],
            "status": "PENDING_REVIEW",
            "review_url": "/knowledge-production/candidates",
        }

    monkeypatch.setattr(knowledge_product, "ingest_source", ingest_source)
    monkeypatch.setattr(knowledge_product, "extract_source", extract_source)

    response = client.post(
        "/api/public-knowledge/knowledge-production/extract?mode=LIVE",
        json={
            "source_id": "source-1",
            "device_type": "NAND Flash",
            "revision_id": "rev-1",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["candidate_count"] == 2
    assert payload["candidate_ids"] == ["KPC-1", "KPC-2"]
    assert payload["formal_knowledge_published"] is False
    assert payload["formal_release_created"] is False
    assert payload["requested_topics"] == ["P/E Cycle", "Data Retention"]

    assert seen["ingest_payload"] == b"%PDF exact public snapshot"
    assert seen["ingest_kwargs"]["source_id"] == "PKR-source-1"
    assert seen["ingest_kwargs"]["revision"] == "rev-1"

    metadata = seen["extract"]["candidate_metadata"]["storage_lifetime"]
    assert metadata["schema_version"] == "storage-lifetime-knowledge/v1"
    assert metadata["device_type"] == "NAND Flash"
    assert metadata["public_source_id"] == "source-1"
    assert metadata["public_source_revision"] == "rev-1"
    assert metadata["formal_consumable"] is False
    assert metadata["semantic_class_status"] == "NEEDS_REVIEW"
    assert "CALCULATION_RULE" in metadata["semantic_class_candidates"]


def test_model_extract_never_runs_in_fixture_mode(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("fixture extraction must stop before any source work")

    monkeypatch.setattr(public_knowledge, "_model_scan_source", forbidden)
    response = client.post(
        "/api/public-knowledge/knowledge-production/extract?mode=FIXTURE_REPLAY",
        json={
            "source_id": "fixture-gd25q64e",
            "device_type": "NAND Flash",
        },
    )
    assert response.status_code == 409
