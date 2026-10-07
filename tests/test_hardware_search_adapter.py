from __future__ import annotations

import pytest

from services.hardware_search_adapter import (
    HardwareSearchAdapter,
    HardwareSearchAdapterError,
)


class StubAdapter(HardwareSearchAdapter):
    def __init__(self):
        super().__init__("http://127.0.0.1:9200")
        self.calls = []
        self.responses = []

    def queue(self, status, payload):
        self.responses.append((status, payload))

    def _request(self, method, path, *, body=None, params=None, allowed_statuses=None):
        self.calls.append(
            {
                "method": method,
                "path": path,
                "body": body,
                "params": params,
                "allowed_statuses": allowed_statuses,
            }
        )
        if not self.responses:
            raise AssertionError("missing stub response")
        return self.responses.pop(0)


def test_invalid_base_url_fails_closed():
    with pytest.raises(HardwareSearchAdapterError) as error:
        HardwareSearchAdapter("localhost:9200")
    assert error.value.code == "SEARCH_ENGINE_URL_INVALID"


def test_health_returns_stable_summary():
    adapter = StubAdapter()
    adapter.queue(
        200,
        {
            "cluster_name": "hardware-search",
            "version": {"number": "3.9.0", "distribution": "opensearch"},
        },
    )
    adapter.queue(
        200,
        {"status": "yellow", "number_of_nodes": 1},
    )

    result = adapter.health()

    assert result == {
        "reachable": True,
        "version": "3.9.0",
        "distribution": "opensearch",
        "cluster_name": "hardware-search",
        "cluster_status": "yellow",
        "number_of_nodes": 1,
    }


def test_ensure_index_is_idempotent():
    adapter = StubAdapter()
    adapter.queue(404, {"status": 404})
    adapter.queue(200, {"acknowledged": True})

    mapping = {"mappings": {"properties": {"knowledge_id": {"type": "keyword"}}}}
    first = adapter.ensure_index("hardware-search-v1", mapping)

    assert first == {"index": "hardware-search-v1", "created": True}
    assert adapter.calls[0]["method"] == "HEAD"
    assert adapter.calls[1]["method"] == "PUT"
    assert adapter.calls[1]["body"] == mapping

    adapter.queue(200, None)
    second = adapter.ensure_index("hardware-search-v1", mapping)
    assert second == {"index": "hardware-search-v1", "created": False}


def test_upsert_uses_document_identity_and_refresh():
    adapter = StubAdapter()
    adapter.queue(201, {"result": "created", "_version": 1})

    result = adapter.upsert(
        "hardware-search-v1",
        "KO-1",
        {"knowledge_id": "KO-1", "title": "MCU reset"},
    )

    assert result["document_id"] == "KO-1"
    assert result["result"] == "created"
    assert adapter.calls[0]["path"].endswith("/_doc/KO-1")
    assert adapter.calls[0]["params"] == {"refresh": True}


def test_search_builds_bm25_and_exact_filters():
    adapter = StubAdapter()
    adapter.queue(
        200,
        {
            "hits": {
                "total": {"value": 1, "relation": "eq"},
                "hits": [{"_id": "KO-1", "_score": 4.2}],
            }
        },
    )

    result = adapter.search(
        "hardware-knowledge-search-active",
        "MCU 偶发复位",
        filters={"component": "MCU", "interface": None},
        limit=10,
    )

    assert result["hits"][0]["_id"] == "KO-1"
    request = adapter.calls[0]
    assert request["method"] == "POST"
    bool_query = request["body"]["query"]["bool"]
    assert bool_query["must"][0]["multi_match"]["operator"] == "and"
    assert bool_query["filter"] == [{"term": {"component": "MCU"}}]


def test_empty_text_uses_match_all_with_filters():
    adapter = StubAdapter()
    adapter.queue(200, {"hits": {"total": {"value": 0}, "hits": []}})

    adapter.search(
        "hardware-knowledge-search-active",
        filters={"business_case_id": "A0207"},
    )

    bool_query = adapter.calls[0]["body"]["query"]["bool"]
    assert bool_query["must"] == [{"match_all": {}}]
    assert bool_query["filter"] == [
        {"term": {"business_case_id": "A0207"}}
    ]


def test_switch_alias_removes_prior_target_then_adds_new_target():
    adapter = StubAdapter()
    adapter.queue(
        200,
        {
            "hardware-search-v1-old": {
                "aliases": {"hardware-knowledge-search-active": {}}
            }
        },
    )
    adapter.queue(200, {"acknowledged": True})

    result = adapter.switch_alias(
        "hardware-knowledge-search-active",
        "hardware-search-v1-new",
    )

    assert result["acknowledged"] is True
    actions = adapter.calls[1]["body"]["actions"]
    assert actions == [
        {
            "remove": {
                "index": "hardware-search-v1-old",
                "alias": "hardware-knowledge-search-active",
            }
        },
        {
            "add": {
                "index": "hardware-search-v1-new",
                "alias": "hardware-knowledge-search-active",
            }
        },
    ]


def test_switch_alias_first_generation_only_adds_alias():
    adapter = StubAdapter()
    adapter.queue(404, {"status": 404})
    adapter.queue(200, {"acknowledged": True})

    adapter.switch_alias(
        "hardware-knowledge-search-active",
        "hardware-search-v1",
    )

    assert adapter.calls[1]["body"]["actions"] == [
        {
            "add": {
                "index": "hardware-search-v1",
                "alias": "hardware-knowledge-search-active",
            }
        }
    ]


def test_delete_missing_index_is_idempotent():
    adapter = StubAdapter()
    adapter.queue(404, {"status": 404})

    result = adapter.delete_index("hardware-search-v1")

    assert result == {
        "index": "hardware-search-v1",
        "deleted": False,
        "missing": True,
    }


def test_invalid_names_and_limit_fail_before_network():
    adapter = StubAdapter()

    with pytest.raises(HardwareSearchAdapterError) as error:
        adapter.ensure_index("bad index", {})
    assert error.value.code == "SEARCH_INDEX_NAME_INVALID"

    with pytest.raises(HardwareSearchAdapterError) as error:
        adapter.search("hardware-search-v1", limit=0)
    assert error.value.code == "SEARCH_LIMIT_INVALID"

    with pytest.raises(HardwareSearchAdapterError) as error:
        adapter.upsert("hardware-search-v1", "", {})
    assert error.value.code == "SEARCH_DOCUMENT_ID_REQUIRED"
