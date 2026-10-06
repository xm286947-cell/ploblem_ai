from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from public_knowledge_rag import app as service
from public_knowledge_rag.store import Store


class FakeProvider:
    provider_id = "test-fake"

    def generate(self, question, contexts):
        return "The public document says NAND retains data for the stated interval. [1]", {
            "provider": self.provider_id, "model": "fixture-model", "digest": "fixture-digest"
        }


def setup_client(monkeypatch, tmp_path):
    store = Store(tmp_path)
    monkeypatch.setattr(service, "store", store)
    monkeypatch.setattr(service, "retriever", service.SQLiteLexicalRetriever(store))
    monkeypatch.setattr(service, "ollama", FakeProvider())
    return TestClient(service.app)


def public_source(client, content="NAND data retention is 10 years at 25 C. P/E endurance is 3000 cycles."):
    return client.post("/sources/import", json={
        "title": "Public NAND datasheet excerpt", "classification": "PUBLIC",
        "source_uri": "https://vendor.example/public/datasheet.pdf", "media_type": "text/plain", "content": content,
    })


def test_public_gate_blocks_non_public_private_urls_and_credentials(monkeypatch, tmp_path):
    client = setup_client(monkeypatch, tmp_path)
    base = {"title": "Public note", "content": "ordinary public text", "media_type": "text/plain"}
    assert client.post("/sources/import", json={**base, "classification": "INTERNAL"}).status_code == 422
    fake_credential = "api" + "_key=" + "fake-value-only"
    assert client.post("/sources/import", json={**base, "classification": "PUBLIC", "content": fake_credential}).status_code == 422
    assert client.post("/sources/import", json={**base, "classification": "PUBLIC", "source_uri": "http://127.0.0.1/a"}).status_code == 422
    assert client.post("/sources/import", json={**base, "classification": "PUBLIC", "source_uri": "http://192.168.1.12/private"}).status_code == 422
    assert client.post("/sources/import", json={**base, "classification": "PUBLIC", "source_uri": "https://example.org/file?token=looks-private"}).status_code == 422
    assert client.post("/search", json={"query": "customer serial number ABCD-12345678-X1"}).status_code == 422
    assert client.post("/search", json={"query": "S/N: 1234567890"}).status_code == 422


def test_source_versioning_and_citation_resolution(monkeypatch, tmp_path):
    client = setup_client(monkeypatch, tmp_path)
    first = public_source(client)
    assert first.status_code == 200
    data = first.json()
    assert data["created"] is True and data["chunk_count"] >= 1
    repeated = public_source(client)
    assert repeated.json()["created"] is False
    revised = public_source(client, "NAND data retention is 10 years at 30 C.")
    assert revised.status_code == 200 and revised.json()["created"] is True
    source = client.get("/sources/" + data["source_id"]).json()
    assert len(source["revisions"]) == 2
    hits = client.post("/search", json={"query": "retention", "top_k": 5}).json()["hits"]
    assert hits
    citation = client.get("/citations/" + hits[0]["hit_id"]).json()
    assert citation["source_id"] == data["source_id"]
    assert citation["source_revision"]
    assert citation["locator"].startswith("char:")
    assert citation["text"]


def test_delete_mistaken_revision_and_source(monkeypatch, tmp_path):
    client = setup_client(monkeypatch, tmp_path)
    first = public_source(client)
    assert first.status_code == 200
    first_data = first.json()

    revised = public_source(client, "NAND data retention is 5 years at 85 C.")
    assert revised.status_code == 200
    revised_data = revised.json()
    assert revised_data["source_id"] == first_data["source_id"]
    assert revised_data["source_revision"] != first_data["source_revision"]

    hits = client.post("/search", json={"query": "85 C", "top_k": 5}).json()["hits"]
    assert hits
    mistaken_citation = hits[0]["hit_id"]

    deleted_revision = client.delete(
        f"/sources/{first_data['source_id']}/revisions/{revised_data['source_revision']}"
    )
    assert deleted_revision.status_code == 200
    assert deleted_revision.json()["formal_knowledge_affected"] is False
    assert deleted_revision.json()["source_deleted"] is False
    assert client.get("/citations/" + mistaken_citation).status_code == 404

    source = client.get("/sources/" + first_data["source_id"])
    assert source.status_code == 200
    assert len(source.json()["revisions"]) == 1

    deleted_source = client.delete("/sources/" + first_data["source_id"])
    assert deleted_source.status_code == 200
    assert deleted_source.json()["formal_knowledge_affected"] is False
    assert client.get("/sources/" + first_data["source_id"]).status_code == 404
    assert client.post("/search", json={"query": "retention", "top_k": 5}).json()["hits"] == []



def test_ask_can_require_chinese_synthesis_and_citation_translations(monkeypatch, tmp_path):
    client = setup_client(monkeypatch, tmp_path)
    public_source(client)

    class CaptureProvider:
        provider_id = "capture"
        question = ""

        def generate(self, question, contexts):
            self.question = question
            return "中文综合结果 [1]", {"provider": self.provider_id, "model": "fixture"}

    provider = CaptureProvider()
    monkeypatch.setattr(service, "active_generation_provider", lambda: provider)

    response = client.post("/ask", json={
        "question": "retention",
        "response_language": "zh-CN",
        "include_citation_translations": True,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "中文综合结果 [1]"
    assert payload["response_language"] == "zh-CN"
    assert payload["citation_translations_requested"] is True
    assert "请使用简体中文回答" in provider.question
    assert "逐条给出对应原文片段的忠实中文翻译" in provider.question
    assert "原问题：retention" in provider.question


def test_live_contract_and_fixture_capture_replay(monkeypatch, tmp_path):
    client = setup_client(monkeypatch, tmp_path)
    imported = public_source(client).json()
    ask = {"question": "What is the data retention interval?", "allowed_source_ids": [imported["source_id"]]}
    answer = client.post("/ask", json=ask)
    assert answer.status_code == 200
    payload = answer.json()
    assert payload["answer"].endswith("[1]")
    assert payload["citations"][0]["source_id"] == imported["source_id"]
    assert payload["model_snapshot"]["provider"] == "test-fake"
    fixture = client.post("/fixtures/capture", json=ask)
    assert fixture.status_code == 200
    captured = fixture.json()
    assert len(captured["sha256"]) == 64
    assert client.get("/fixtures/" + captured["fixture_id"]).json()["sha256"] == captured["sha256"]
    replay = client.post("/fixtures/replay", json={"fixture_id": captured["fixture_id"], "question": ask["question"]})
    assert replay.status_code == 200
    assert replay.json()["answer"] == captured["response"]["answer"]
    assert replay.json()["mode"] == "FIXTURE_REPLAY"
    assert client.post("/fixtures/replay", json={"fixture_id": captured["fixture_id"], "question": "different"}).status_code == 409


def test_config_hash_and_provider_slots(monkeypatch, tmp_path):
    client = setup_client(monkeypatch, tmp_path)
    snapshot = client.get("/config").json()
    assert len(snapshot["config_hash"]) == 64
    assert snapshot["config"]["source_class_gate"] == "PUBLIC_ONLY"
    assert "api_key" not in str(snapshot).lower()
    assert client.get("/providers/manual/status").json()["policy"] == "fail-closed"
    assert client.get("/providers/embedding/status").json()["status"] == "slot-available-unconfigured"
    assert client.get("/health").json()["status"] == "ok"


def test_ask_fails_closed_if_provider_is_down(monkeypatch, tmp_path):
    client = setup_client(monkeypatch, tmp_path)
    public_source(client)

    class DownProvider:
        def generate(self, question, contexts):
            raise service.ProviderUnavailable("Remote Ollama provider unavailable; request failed closed.")

    monkeypatch.setattr(service, "ollama", DownProvider())
    response = client.post("/ask", json={"question": "What does this public datasheet say?"})
    assert response.status_code == 503
    assert "failed closed" in response.json()["detail"]


def test_model_digest_mismatch_fails_closed(monkeypatch):
    provider = service.OllamaProvider(service.settings)
    monkeypatch.setattr(provider, "models", lambda: [{"name": service.settings.ollama_model, "digest": "wrong"}])
    try:
        provider.generate("generic public question", [])
    except service.ProviderUnavailable as exc:
        assert "failed closed" in str(exc)
    else:
        raise AssertionError("mismatched model digest was accepted")


def test_public_query_allows_engineering_internal_ecc_but_blocks_explicit_private_ids(
    monkeypatch,
    tmp_path,
):
    client = setup_client(monkeypatch, tmp_path)
    imported = public_source(
        client,
        "NAND device supports internal ECC and on-die ECC status reporting.",
    )
    assert imported.status_code == 200

    public_query = client.post(
        "/search",
        json={"query": "internal ECC", "top_k": 5},
    )
    assert public_query.status_code == 200
    assert public_query.json()["hits"]

    assert client.post(
        "/search",
        json={"query": "internal project id: ABC123"},
    ).status_code == 422
    assert client.post(
        "/search",
        json={"query": "customer code: CUST-7788"},
    ).status_code == 422
    assert client.post(
        "/search",
        json={"query": "S/N: 1234567890"},
    ).status_code == 422



def test_active_provider_health_uses_openai_compatible_when_configured(monkeypatch, tmp_path):
    client = setup_client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        service,
        "settings",
        SimpleNamespace(
            provider_type="openai_compatible",
            openai_protocol="chat_completions",
            openai_model="GLM-5.3-Flash",
            ollama_model="unused",
        ),
    )
    monkeypatch.setattr(service, "active_api_key", "configured-test-key")

    class HealthyOpenAI:
        provider_id = "openai_compatible"

        def test_connection(self):
            return {
                "test_response_received": True,
                "model": "glm-5.3-flash",
            }

    monkeypatch.setattr(service, "openai_compatible", HealthyOpenAI())

    response = client.get("/providers/active/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "openai_compatible"
    assert payload["status"] == "ok"
    assert payload["protocol"] == "chat_completions"
    assert payload["model"] == "GLM-5.3-Flash"
    assert payload["reported_model"] == "glm-5.3-flash"
    assert payload["model_identity_match"] is True
    assert payload["identity_normalization"] == "strip+casefold"
    assert payload["test_response_received"] is True


def test_active_provider_health_does_not_probe_ollama_for_openai(monkeypatch, tmp_path):
    client = setup_client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        service,
        "settings",
        SimpleNamespace(
            provider_type="openai_compatible",
            openai_protocol="chat_completions",
            openai_model="GLM-5.3-Flash",
            ollama_model="unused",
        ),
    )
    monkeypatch.setattr(service, "active_api_key", "configured-test-key")

    class HealthyOpenAI:
        provider_id = "openai_compatible"

        def test_connection(self):
            return {"test_response_received": True, "model": "GLM-5.3-Flash"}

    class ExplodingOllama:
        def version(self):
            raise AssertionError("Ollama must not be probed")

    monkeypatch.setattr(service, "openai_compatible", HealthyOpenAI())
    monkeypatch.setattr(service, "ollama", ExplodingOllama())

    response = client.get("/providers/active/health")
    assert response.status_code == 200



def test_active_provider_health_rejects_real_model_identity_change(monkeypatch, tmp_path):
    client = setup_client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        service,
        "settings",
        SimpleNamespace(
            provider_type="openai_compatible",
            openai_protocol="chat_completions",
            openai_model="GLM-5.3-Flash",
            ollama_model="unused",
        ),
    )
    monkeypatch.setattr(service, "active_api_key", "configured-test-key")

    class WrongModelOpenAI:
        provider_id = "openai_compatible"

        def test_connection(self):
            return {
                "test_response_received": True,
                "model": "GLM-4.7-Flash",
            }

    monkeypatch.setattr(service, "openai_compatible", WrongModelOpenAI())

    response = client.get("/providers/active/health")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "MODEL_IDENTITY_MISMATCH"
    assert detail["configured_model"] == "GLM-5.3-Flash"
    assert detail["reported_model"] == "GLM-4.7-Flash"
