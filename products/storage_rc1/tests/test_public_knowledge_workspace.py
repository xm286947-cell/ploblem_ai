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
        if path == "/sources":
            return {"sources": []}
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
                "hits": [
                    {
                        "source_id": "source-1",
                        "source_revision": "rev-1",
                        "locator": {
                            "page": 10,
                            "section": "Reliability",
                        },
                    }
                ],
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
                "hits": [
                    {
                        "source_id": "source-1",
                        "source_revision": "rev-1",
                        "locator": {
                            "page": 25,
                            "section": "Data Retention",
                        },
                    }
                ],
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

    bridge = seen["extract"]["candidate_metadata"]["storage_source_bridge"]
    assert bridge["schema_version"] == "storage-lifetime-knowledge/v1"
    assert bridge["public_source_id"] == "source-1"
    assert bridge["public_source_revision"] == "rev-1"

    class ObjectType:
        value = "FACT"

    class Draft:
        title = "P/E Cycle endurance fact"
        object_type = ObjectType()

    enrichment = seen["extract"]["candidate_enricher"](Draft())
    metadata = enrichment["metadata"]["storage_lifetime"]
    assert metadata["schema_version"] == "storage-lifetime-knowledge/v1"
    assert metadata["device_type"] == "NAND Flash"
    assert metadata["public_source_id"] == "source-1"
    assert metadata["public_source_revision"] == "rev-1"
    assert metadata["canonical_parameters"] == ["pe_cycles"]
    assert metadata["parameter_binding_status"] == "BOUND"
    assert metadata["parameter_binding_basis"] == "CANDIDATE_TEXT"
    assert metadata["formal_consumable"] is False
    assert metadata["semantic_class_status"] == "NEEDS_REVIEW"
    assert set(metadata["semantic_class_candidates"]) == {
        "PARAMETER_DEFINITION",
        "CALCULATION_RULE",
    }
    assert "storage-parameter:pe_cycles" in enrichment["tags"]
    assert (
        "storage-semantic-candidate:CALCULATION_RULE"
        in enrichment["tags"]
    )

    class Location:
        page = 10
        section = "Reliability"
        source_anchor = "page:10"

    class EvidenceBoundDraft:
        title = "Endurance qualification guidance"
        summary = "Qualification limits for the memory technology."
        content = "The qualification guidance is evidence backed."
        tags = []
        object_type = ObjectType()
        evidence_locations = [Location()]

    evidence_enrichment = seen["extract"]["candidate_enricher"](
        EvidenceBoundDraft()
    )
    evidence_metadata = evidence_enrichment["metadata"][
        "storage_lifetime"
    ]
    assert evidence_metadata["canonical_parameters"] == ["pe_cycles"]
    assert evidence_metadata["parameter_binding_status"] == "BOUND"
    assert (
        evidence_metadata["parameter_binding_basis"]
        == "EVIDENCE_LOCATOR"
    )
    assert "storage-parameter:pe_cycles" in evidence_enrichment["tags"]

    # Multiple model parameters can legitimately share one datasheet table/page.
    # Keep that ambiguity visible for human review instead of choosing one.
    scan["parameters"][1]["hits"][0]["locator"] = {
        "page": 10,
        "section": "Reliability",
    }
    ambiguous_enrichment = seen["extract"]["candidate_enricher"](
        EvidenceBoundDraft()
    )
    ambiguous_metadata = ambiguous_enrichment["metadata"][
        "storage_lifetime"
    ]
    assert ambiguous_metadata["canonical_parameters"] == [
        "pe_cycles",
        "retention",
    ]
    assert ambiguous_metadata["parameter_binding_status"] == "AMBIGUOUS"
    assert (
        ambiguous_metadata["parameter_binding_basis"]
        == "EVIDENCE_LOCATOR"
    )


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


def test_file_import_forwards_publisher_provenance(monkeypatch):
    seen = {}

    def capture(mode, path, fields, filename, content, media_type, base_url=None):
        seen.update(
            mode=mode,
            path=path,
            fields=fields,
            filename=filename,
            content=content,
            media_type=media_type,
        )
        return {"source_id": "s1", "source_sha256": "sha"}

    monkeypatch.setattr(public_knowledge, "_request_file", capture)
    response = client.post(
        "/api/public-knowledge/sources/import-file?mode=LIVE",
        data={
            "title": "KIOXIA SLC NAND",
            "publisher": "KIOXIA Corporation",
            "classification": "PUBLIC",
        },
        files={"file": ("kioxia.pdf", b"%PDF", "application/pdf")},
    )

    assert response.status_code == 200
    assert seen["fields"]["publisher"] == "KIOXIA Corporation"
    assert seen["fields"]["title"] == "KIOXIA SLC NAND"


def test_model_extract_requires_source_authority_before_kp_handoff(
    monkeypatch,
):
    from storage_life import knowledge_product

    monkeypatch.setattr(
        public_knowledge,
        "_model_scan_source",
        lambda body, mode, base_url: {
            "model_version": "storage-lifetime-knowledge/v1",
            "device_type": "NAND Flash",
            "coverage": {
                "parameter_count": 1,
                "found_count": 1,
                "not_found_count": 0,
                "role_gap_count": 0,
            },
            "parameters": [
                {
                    "canonical_name": "cell_type",
                    "coverage_status": "FOUND",
                    "queries_tried": ["Cell Type"],
                    "knowledge_requirements": ["PARAMETER_DEFINITION"],
                    "scenario_consumers": ["S1", "S2"],
                }
            ],
        },
    )
    monkeypatch.setattr(
        public_knowledge,
        "_public_source_detail",
        lambda source_id, mode, base_url: (
            {
                "source_id": source_id,
                "title": "Unknown publisher source",
                "source_class": "PUBLIC",
                "source_uri": None,
                "publisher": None,
            },
            [
                {
                    "revision_id": "rev-1",
                    "original_filename": "source.pdf",
                    "media_type": "application/pdf",
                }
            ],
        ),
    )
    monkeypatch.setattr(
        public_knowledge,
        "source_snapshot",
        lambda source_id, revision_id, mode, base_url: public_knowledge.Response(
            content=b"%PDF public source",
            media_type="application/pdf",
        ),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("unattributed source must not enter Knowledge Production")

    monkeypatch.setattr(knowledge_product, "ingest_source", forbidden)
    response = client.post(
        "/api/public-knowledge/knowledge-production/extract?mode=LIVE",
        json={
            "source_id": "source-no-publisher",
            "device_type": "NAND Flash",
            "revision_id": "rev-1",
        },
    )

    assert response.status_code == 422
    assert (
        response.json()["detail"]["code"]
        == "PUBLIC_SOURCE_PUBLISHER_REQUIRED"
    )



def test_model_scan_filters_weak_lexical_hits_by_evidence_anchor(monkeypatch):
    calls = []

    def hit(hit_id, text, page):
        return {
            "hit_id": hit_id,
            "source_id": "kioxia-real",
            "source_revision": "rev-real",
            "locator": {
                "page": page,
                "section": "Document",
                "type": "pdf_text",
            },
            "text": text,
            "score": 1.0,
        }

    def request(mode, path, payload=None, base_url=None):
        if path == "/sources/kioxia-real":
            return {
                "source": {
                    "source_id": "kioxia-real",
                    "title": "KIOXIA endurance",
                    "classification": "PUBLIC",
                    "revision": "Rev. 1.0",
                }
            }
        if path != "/search":
            raise AssertionError(path)

        calls.append(dict(payload))
        assert payload["filters"] == {"source_id": "kioxia-real"}
        # W4 found that a small Top-K lets generic "data" chunks hide the
        # actual Data Retention sentence. Retrieval may be broad; evidence
        # acceptance below must remain strict.
        assert payload["top_k"] >= 20
        query = str(payload["query"]).casefold()
        if "data retention" in query:
            return {
                "hits": [
                    hit("noise-1", "Data is stored persistently.", 1),
                    hit("noise-2", "Data center SSD applications.", 2),
                    hit("noise-3", "Host data is written to NAND.", 3),
                    hit(
                        "retention-real",
                        "Higher WAF may affect data retention and shorten NAND flash memory life.",
                        4,
                    ),
                ]
            }
        if "ecc status" in query:
            return {
                "hits": [
                    hit(
                        "ecc-noise",
                        "ECC routines within the flash controller may correct common bit errors.",
                        3,
                    )
                ]
            }
        if "program fail" in query:
            return {
                "hits": [
                    hit(
                        "program-noise",
                        "A Program/Erase cycle is generated as data is written.",
                        3,
                    ),
                    hit(
                        "failed-drive-noise",
                        "Under-specified endurance may require failed SSDs to be replaced.",
                        2,
                    ),
                ]
            }
        if "runtime bad block" in query:
            return {
                "hits": [
                    hit(
                        "block-noise",
                        "Valid data may be rewritten from several NAND blocks.",
                        3,
                    )
                ]
            }
        # bilingual display-label fallback may still retrieve noise; it must
        # not become evidence without the strict English/canonical anchor.
        return {"hits": []}

    monkeypatch.setattr(public_knowledge, "_request", request)
    response = client.post(
        "/api/public-knowledge/model-scan?mode=LIVE",
        json={
            "source_id": "kioxia-real",
            "device_type": "NAND Flash",
            "top_k_per_parameter": 3,
            "parameter_names": [
                "retention",
                "ecc_status",
                "program_fail",
                "runtime_bad_block",
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    by_name = {
        item["canonical_name"]: item
        for item in payload["parameters"]
    }

    assert by_name["retention"]["coverage_status"] == "FOUND"
    assert [
        item["hit_id"] for item in by_name["retention"]["hits"]
    ] == ["retention-real"]
    assert by_name["retention"]["evidence_match_policy"] == (
        "MODEL_ANCHOR_REQUIRED"
    )

    # These source excerpts discuss nearby concepts, but do not define the
    # model parameter itself. They must remain knowledge gaps.
    for name in ("ecc_status", "program_fail", "runtime_bad_block"):
        assert by_name[name]["coverage_status"] == "NOT_FOUND"
        assert by_name[name]["hits"] == []

    assert payload["coverage"]["found_count"] == 1
    assert payload["coverage"]["not_found_count"] == 3



def test_model_extract_surfaces_safe_provider_transport_action(
    monkeypatch,
):
    from storage_life import knowledge_product

    monkeypatch.setattr(
        public_knowledge,
        "_model_scan_source",
        lambda body, mode, base_url: {
            "model_version": "storage-lifetime-knowledge/v1",
            "device_type": "NAND Flash",
            "coverage": {
                "parameter_count": 1,
                "found_count": 1,
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
                    ],
                    "scenario_consumers": ["S1", "S2", "S3"],
                    "hits": [
                        {
                            "source_id": "source-provider",
                            "source_revision": "rev-provider",
                            "locator": {
                                "page": 3,
                                "section": "Document",
                                "type": "pdf_text",
                            },
                            "text": "Program/Erase (P/E) cycle",
                            "score": 1.0,
                        }
                    ],
                }
            ],
        },
    )
    monkeypatch.setattr(
        public_knowledge,
        "_public_source_detail",
        lambda source_id, mode, base_url: (
            {
                "source_id": source_id,
                "title": "KIOXIA endurance",
                "source_class": "PUBLIC",
                "publisher": "KIOXIA Corporation",
                "source_uri": "https://business.kioxia.com/",
            },
            [
                {
                    "revision_id": "rev-provider",
                    "original_filename": "kioxia.pdf",
                    "media_type": "application/pdf",
                }
            ],
        ),
    )
    monkeypatch.setattr(
        public_knowledge,
        "source_snapshot",
        lambda source_id, revision_id, mode, base_url:
        public_knowledge.Response(
            content=b"%PDF exact public snapshot",
            media_type="application/pdf",
        ),
    )
    monkeypatch.setattr(
        knowledge_product,
        "ingest_source",
        lambda *args, **kwargs: {
            "source_document": {
                "source_id": "PKR-source-provider",
                "source_version": "rev-provider",
            }
        },
    )

    class ProviderTransportError(RuntimeError):
        code = "PROVIDER_TRANSPORT"

        def __str__(self):
            return (
                "http://secret.internal/v1 "
                "api_key=must-not-escape"
            )

    monkeypatch.setattr(
        knowledge_product,
        "extract_source",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ProviderTransportError()
        ),
    )

    response = client.post(
        "/api/public-knowledge/knowledge-production/extract?mode=LIVE",
        json={
            "source_id": "source-provider",
            "device_type": "NAND Flash",
            "revision_id": "rev-provider",
            "parameter_names": ["pe_cycles"],
        },
    )

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "PROVIDER_TRANSPORT"
    assert "AI Provider" in detail["message"]
    assert detail["retryable"] is True
    serialized = str(detail)
    assert "secret.internal" not in serialized
    assert "must-not-escape" not in serialized


def test_live_qa_can_scope_chinese_translation_to_public_source(monkeypatch):
    seen = {}

    def request(mode, path, payload=None, base_url=None):
        seen.update(mode=mode, path=path, payload=payload)
        return {"answer": "中文译文", "citations": []}

    monkeypatch.setattr(public_knowledge, "_request", request)
    response = client.post("/api/public-knowledge/ask?mode=LIVE", json={
        "question": "program endurance",
        "response_language": "zh-CN",
        "include_citation_translations": True,
        "allowed_source_ids": ["source-public-001"],
    })
    assert response.status_code == 200
    assert seen["path"] == "/ask"
    assert seen["payload"]["response_language"] == "zh-CN"
    assert seen["payload"]["include_citation_translations"] is True
    assert seen["payload"]["allowed_source_ids"] == ["source-public-001"]


def test_live_qa_rejects_invalid_translation_source_id_before_forwarding(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid source id must not be forwarded")

    monkeypatch.setattr(public_knowledge, "_request", forbidden)
    response = client.post("/api/public-knowledge/ask?mode=LIVE", json={
        "question": "program endurance",
        "response_language": "zh-CN",
        "include_citation_translations": True,
        "allowed_source_ids": ["x" * 201],
    })
    assert response.status_code == 422


def test_public_knowledge_ui_is_chinese_first_and_keeps_original_evidence():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "storage_life" / "index.html").read_text(encoding="utf-8")
    assert "独立问答（QA）" in html
    assert "知识建议" in html
    assert "服务健康" in html
    assert "中文翻译与综合解读" in html
    assert "翻译成中文" in html
    assert "查看英文原文证据" in html
    assert "allowed_source_ids:[sourceId]" in html
    assert "英文 Citation 与原始文件仍是正式证据" in html
