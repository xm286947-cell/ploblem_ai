from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path


base = os.getenv("PKR_BASE_URL", "http://127.0.0.1:9000").rstrip("/")


def request(method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"} if data else {}, method=method)
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def main() -> None:
    result: dict[str, object] = {"base_url": base}
    result["health"] = request("GET", "/health")
    result["config"] = request("GET", "/config")
    result["ollama_health"] = request("GET", "/providers/ollama/health")
    result["ollama_models"] = request("GET", "/providers/ollama/models")
    imported = request("POST", "/sources/import", {
        "title": "Synthetic public infrastructure smoke record",
        "classification": "PUBLIC",
        "source_uri": "https://example.org/public/infra-smoke",
        "media_type": "text/plain",
        "content": "The public infrastructure smoke record states that the smoke value is 42.",
    })
    result["import"] = imported
    sources = request("GET", "/sources")
    result["source_list_count"] = len(sources.get("sources", []))
    result["source_detail"] = request("GET", "/sources/" + str(imported["source_id"]))
    query = "What is the smoke value?"
    result["search"] = request("POST", "/search", {"query": "smoke value", "top_k": 10})
    answer_request = {"question": query, "allowed_source_ids": [imported["source_id"]]}
    result["live_answer"] = request("POST", "/ask", answer_request)
    citations = result["live_answer"].get("citations", [])
    if not citations:
        raise RuntimeError("LIVE response had no citation for the synthetic public smoke record")
    result["citation_detail"] = request("GET", "/citations/" + str(citations[0]["citation_id"]))
    fixture = request("POST", "/fixtures/capture", answer_request)
    result["fixture_capture"] = {k: v for k, v in fixture.items() if k != "response"}
    result["fixture_readback"] = {k: v for k, v in request("GET", "/fixtures/" + str(fixture["fixture_id"])).items() if k != "response"}
    replay = request("POST", "/fixtures/replay", {"fixture_id": fixture["fixture_id"], "question": query})
    result["fixture_replay"] = replay
    assert replay["answer"] == fixture["response"]["answer"]
    assert replay["citations"] == fixture["response"]["citations"]
    Path("evidence").mkdir(exist_ok=True)
    Path("evidence/e2e-smoke.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": "PASS", "source_id": imported["source_id"],
                      "citation_id": citations[0]["citation_id"], "fixture_id": fixture["fixture_id"],
                      "model_snapshot": result["live_answer"].get("model_snapshot"),
                      "config_hash": result["config"].get("config_hash"), "evidence": "evidence/e2e-smoke.json"}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, OSError, RuntimeError, urllib.error.URLError) as exc:
        print(json.dumps({"result": "FAIL", "error": type(exc).__name__, "detail": str(exc)}))
        raise SystemExit(1)
