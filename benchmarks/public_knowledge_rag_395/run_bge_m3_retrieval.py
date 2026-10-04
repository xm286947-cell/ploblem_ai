from __future__ import annotations

import hashlib
import json
import os
import resource
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
RUN = Path(os.getenv("RAG395_RUN_DIR", "/Users/xiamin/dev/存储/public-knowledge-rag-395-run"))
sys.path.insert(0, str(REPO))

from public_knowledge_rag.tech_selection import HybridRRFIndex, QdrantDenseIndex, SQLiteFTS5Index

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://192.168.1.100:11434").rstrip("/")
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6339")
MODEL = "bge-m3:latest"
COLLECTION = "public_knowledge_rag_395"
EXPECTED_CHUNKS_SHA256 = "71fb5b2d55ae934b267da72a2364a5e8301ce0c886c6f6ad8fedc2e5647b6189"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def ollama_json(endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{OLLAMA_URL}{endpoint}", data=data,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(request, timeout=900) as response:
        return json.loads(response.read())


class BgeM3OllamaProvider:
    """BGE-M3 uses raw query/document text; unlike the Qwen baseline, add no prefix."""

    def __init__(self) -> None:
        self.base_url = OLLAMA_URL
        self.model = MODEL
        self.timeout = 900
        self.calls = 0
        self.items = 0
        self.seconds = 0.0

    def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        if not texts:
            return []
        if query:
            # BGE-M3 has no instruction prefix in the frozen experiment protocol.
            inputs = texts
        else:
            inputs = texts
        payload = json.dumps({"model": self.model, "input": inputs, "truncate": False}).encode()
        request = urllib.request.Request(
            f"{self.base_url}/api/embed", data=payload,
            headers={"Content-Type": "application/json"},
        )
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read())
        self.calls += 1
        self.items += len(texts)
        self.seconds += time.monotonic() - started
        vectors = result.get("embeddings", [])
        if len(vectors) != len(texts):
            raise RuntimeError(f"Embedding batch cardinality mismatch: {len(vectors)} != {len(texts)}")
        dims = {len(vector) for vector in vectors}
        if dims != {1024}:
            raise RuntimeError(f"Expected 1024-dimensional vectors, got {sorted(dims)}")
        return vectors


def source_class(source: dict[str, Any]) -> str:
    sid, title = source["source_id"].upper(), source["title"].upper()
    if "NVMEXPRESS-BASE" in sid or "BASE SPECIFICATION" in title:
        return "standard"
    if any(token in sid for token in ("NVMECLI", "LINUX-", "SQLITE-", "LITTLEFS-")):
        return "software"
    return "datasheet"


def docker_stats() -> str | None:
    try:
        return subprocess.check_output(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}} {{.CPUPerc}}"],
            text=True, stderr=subprocess.STDOUT, timeout=20,
        ).strip()
    except Exception as exc:
        return f"unavailable: {type(exc).__name__}: {exc}"


def main() -> int:
    evidence, data = RUN / "evidence", RUN / "data"
    chunks_path = RUN / "parsed/hybrid_chunks.json"
    queries_path = RUN / "golden_queries.json"
    chunks = read_json(chunks_path)
    queries_doc = read_json(queries_path)
    queries = queries_doc["queries"]
    manifest = read_json(RUN / "source_manifest.json")
    chunking = read_json(evidence / "chunking_summary.json")
    sources = chunking["sources"]
    if sha256(chunks_path) != EXPECTED_CHUNKS_SHA256 or len(chunks) != 2395:
        raise SystemExit("Frozen #391 chunk input mismatch; refusing benchmark")
    if len(manifest["sources"]) != 21 or len(queries) != 60:
        raise SystemExit("Frozen source/query count mismatch; refusing benchmark")
    if not (data / "chunks.sqlite3").is_file():
        raise SystemExit("Frozen #391 SQLite FTS5 copy is missing")

    tags = ollama_json("/api/tags")["models"]
    model_row = next((m for m in tags if m["name"] == MODEL), None)
    if not model_row:
        raise SystemExit(f"Model {MODEL} not installed after permitted pull")
    model_show = ollama_json("/api/show", {"name": MODEL})
    pre_ps = ollama_json("/api/ps")
    pre_stats = docker_stats()
    client = QdrantDenseIndex(QDRANT_URL, COLLECTION, BgeM3OllamaProvider(), 1024)
    if client.client.get_collection(COLLECTION).points_count:
        raise SystemExit(f"Fresh collection {COLLECTION} is not empty")

    provider = client.embedding
    # Keep request payloads below the remote Ollama context envelope. This is
    # transport batching only; chunk text, ordering, and retrieval parameters
    # remain identical to #391.
    batch_size = 4
    batches = []
    started = time.monotonic()
    for offset in range(0, len(chunks), batch_size):
        batch = chunks[offset:offset + batch_size]
        try:
            vectors = provider.embed([row["text"] for row in batch])
        except Exception as exc:
            write_json(evidence / "failure.json", {
                "task": "PUBLIC-KNOWLEDGE-RAG-BGE-M3-RESELECT-001",
                "phase": "REEMBED",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "batch_offset": offset,
                "batch_count": len(batch),
                "first_chunk_id": batch[0]["chunk_id"] if batch else None,
                "first_chunk_text_sha256": hashlib.sha256(batch[0]["text"].encode()).hexdigest() if batch else None,
                "chunks_processed_successfully": provider.items,
                "qdrant_collection": COLLECTION,
                "qdrant_points_after_failure": client.client.get_collection(COLLECTION).points_count,
                "generation_calls": 0,
                "chunk_corpus_changed": False,
            })
            raise
        client.upsert(batch, vectors)
        batches.append({"offset": offset, "count": len(batch), "dimensions": len(vectors[0])})
        if len(batches) % 10 == 0 or offset + len(batch) == len(chunks):
            write_json(evidence / "reembed_progress.json", {
                "completed_chunks": offset + len(batch), "total_chunks": len(chunks),
                "embedding_calls": provider.calls, "elapsed_seconds": round(time.monotonic() - started, 2),
                "max_rss_bytes": rss_bytes(),
            })
            print(json.dumps({"phase": "reembed", "completed": offset + len(batch), "total": len(chunks), "seconds": round(time.monotonic() - started, 1)}), flush=True)

    reembed_seconds = time.monotonic() - started
    reembed_http_calls = provider.calls
    reembed_items = provider.items
    reembed_http_seconds = provider.seconds
    if provider.items != len(chunks) or client.client.get_collection(COLLECTION).points_count != len(chunks):
        raise SystemExit("Re-embedding point count mismatch")
    post_ps = ollama_json("/api/ps")
    post_stats = docker_stats()

    source_by_material = {row["material_id"]: row for row in sources}
    source_by_id = {row["source_id"]: row for row in sources}
    revision_by_id = {row["source_id"]: row["source_revision"] for row in sources}
    chunk_by_id = {row["chunk_id"]: row for row in chunks}
    metadata = [{
        "material_id": row["material_id"], "source_id": row["source_id"],
        "source_revision": row["source_revision"], "title": row["title"],
        "media_type": row["media_type"], "source_class": row["source_class"],
    } for row in sources]
    lexical = SQLiteFTS5Index(data / "chunks.sqlite3")
    retriever = HybridRRFIndex(client, lexical, 60, 20, 20, metadata)
    result_rows = []
    retrieval_started = time.monotonic()
    for i, query in enumerate(queries, 1):
        hits = retriever.search(query["query"], 10)
        expected = set(query["source_ids"])
        expected_classes = {source_by_material[mid]["source_class"] for mid in query["source_materials"]}
        filter_evidence = retriever.last_search_filter
        allowed = set(filter_evidence.get("source_ids", [])) if filter_evidence.get("applied") else None
        wrong_filter = [h.hit_id for h in hits if allowed is not None and h.source_id not in allowed]
        wrong_revision = [h.hit_id for h in hits if revision_by_id.get(h.source_id) != h.source_revision]
        locator_errors = []
        for hit in hits:
            try:
                loc = json.loads(hit.locator)
                source = source_by_id.get(hit.source_id, {})
                frozen_chunk = chunk_by_id.get(hit.hit_id)
                refs = loc.get("element_refs", [])
                start_page, end_page = loc.get("page_start"), loc.get("page_end")
                location_valid = bool(refs) or bool(
                    start_page and end_page and start_page >= 1 and end_page >= start_page
                    and end_page <= source.get("page_count", 0)
                )
                if not (frozen_chunk and frozen_chunk["source_id"] == hit.source_id
                        and frozen_chunk["source_revision"] == hit.source_revision
                        and frozen_chunk["locator"] == hit.locator and location_valid):
                    locator_errors.append(hit.hit_id)
            except Exception:
                locator_errors.append(hit.hit_id)
        hit_source_ids = [hit.source_id for hit in hits]
        result_rows.append({
            "id": query["id"], "query": query["query"],
            "expected_materials": query["source_materials"], "expected_source_ids": sorted(expected),
            "expected_source_classes": sorted(expected_classes), "metadata_filter": filter_evidence,
            "wrong_explicit_source_hits": wrong_filter, "wrong_revision_hits": wrong_revision,
            "unresolvable_locators": locator_errors,
            "expected_source_all_found_at": {
                str(k): expected.issubset(set(hit_source_ids[:k])) for k in (1, 3, 5, 10)
            },
            "expected_source_any_found_at": {
                str(k): bool(expected.intersection(hit_source_ids[:k])) for k in (1, 3, 5, 10)
            },
            "wrong_source_top1": bool(hits and hits[0].source_id not in expected),
            "top10": [{"hit_id": h.hit_id, "source_id": h.source_id,
                       "source_revision": h.source_revision, "locator": h.locator,
                       "score": h.score, "text": h.text} for h in hits],
        })
        if i % 10 == 0:
            write_json(evidence / "retrieval_progress.json", {"completed": i, "total": 60, "rows": result_rows})
            print(json.dumps({"phase": "retrieval", "completed": i, "total": 60}), flush=True)

    def score(rows: list[dict[str, Any]], k: int) -> dict[str, Any]:
        hits = sum(bool(row["expected_source_all_found_at"][str(k)]) for row in rows)
        any_hits = sum(bool(row["expected_source_any_found_at"][str(k)]) for row in rows)
        return {"all_expected_source_hits": hits, "any_expected_source_hits": any_hits,
                "queries": len(rows), "recall": hits / len(rows) if rows else None,
                "any_source_recall": any_hits / len(rows) if rows else None}

    scores = {str(k): score(result_rows, k) for k in (1, 3, 5, 10)}
    category_scores = {
        name: {str(k): score([r for r in result_rows if name in r["expected_source_classes"]], k)
               for k in (1, 3, 5, 10)}
        for name in ("datasheet", "standard", "software")
    }
    gate_overall = scores["10"]["recall"] >= 0.90
    gate_categories = all(v["10"]["recall"] is not None and v["10"]["recall"] >= 0.90
                          for rows in category_scores.values() for v in [rows["10"]])
    wrong_revision_count = sum(len(r["wrong_revision_hits"]) for r in result_rows)
    filter_leaks = sum(len(r["wrong_explicit_source_hits"]) for r in result_rows)
    locator_errors = sum(len(r["unresolvable_locators"]) for r in result_rows)
    phase_pass = gate_overall and gate_categories and wrong_revision_count == 0 and filter_leaks == 0 and locator_errors == 0
    if phase_pass:
        decision = "EMBEDDING_SELECTED"
    elif gate_overall:
        decision = "ADD_RERANKER"
    else:
        decision = "ADD_RERANKER"

    collection_info = client.client.get_collection(COLLECTION)
    final = {
        "task": "PUBLIC-KNOWLEDGE-RAG-BGE-M3-RESELECT-001", "phase": "RETRIEVAL_ONLY",
        "generation_calls": 0, "model": MODEL, "model_digest": model_row["digest"],
        "model_size_bytes": model_row["size"], "model_details": model_row.get("details", {}),
        "model_show": model_show, "model_ps_before": pre_ps, "model_ps_after": post_ps,
        "embedding_dimensions": 1024, "query_prefix": None,
        "frozen_inputs": {"source_count": 21, "query_count": 60, "chunk_count": 2395,
            "chunk_sha256": sha256(chunks_path), "query_sha256": sha256(queries_path),
            "source_manifest_sha256": sha256(RUN / "source_manifest.json"),
            "sqlite_fts5_sha256": sha256(data / "chunks.sqlite3")},
        "candidate_depths": {"dense": 20, "lexical": 20, "rrf_k": 60, "top_k": 10},
        "overall": scores, "categories": category_scores,
        "wrong_source_top1": sum(bool(r["wrong_source_top1"]) for r in result_rows),
        "wrong_revision_hits": wrong_revision_count, "explicit_filter_leaks": filter_leaks,
        "unresolvable_top10_locators": locator_errors,
        "filters_applied": sum(bool(r["metadata_filter"].get("applied")) for r in result_rows),
        "collection": {"name": COLLECTION, "points": collection_info.points_count,
            "status": str(collection_info.status), "vectors_count": getattr(collection_info, "vectors_count", None),
            "indexed_vectors_count": getattr(collection_info, "indexed_vectors_count", None),
            "segments_count": getattr(collection_info, "segments_count", None),
            "disk_data_size": getattr(collection_info, "disk_data_size", None),
            "ram_data_size": getattr(collection_info, "ram_data_size", None)},
        "resource": {"reembed_seconds": round(reembed_seconds, 2),
            "embedding_http_calls_for_chunks": reembed_http_calls,
            "embedding_items_for_chunks": reembed_items,
            "embedding_http_seconds_for_chunks": round(reembed_http_seconds, 2),
            "embedding_http_calls_for_queries": provider.calls - reembed_http_calls,
            "embedding_items_for_queries": provider.items - reembed_items,
            "retrieval_seconds": round(time.monotonic() - retrieval_started, 2),
            "mac_max_rss_bytes": rss_bytes(), "docker_stats_before": pre_stats,
            "docker_stats_after": post_stats},
        "gate": {"overall_recall_at_10_ge_90": gate_overall,
            "all_category_recall_at_10_ge_90": gate_categories,
            "wrong_revision_zero": wrong_revision_count == 0,
            "explicit_filter_leaks_zero": filter_leaks == 0,
            "citation_locator_resolvable_100_percent": locator_errors == 0,
            "phase_pass": phase_pass, "decision": decision},
        "rows": result_rows,
    }
    write_json(evidence / "retrieval_results.json", final)
    summary = [
        "TASK=PUBLIC-KNOWLEDGE-RAG-BGE-M3-RESELECT-001", "PHASE=RETRIEVAL_ONLY",
        f"MODEL={MODEL}", f"MODEL_DIGEST={model_row['digest']}",
        f"CHUNKS=2395", f"QUERIES=60", f"GENERATION_CALLS=0",
    ]
    for k, value in scores.items():
        summary.append(f"ALL_EXPECTED_SOURCES_AT_{k}={value['all_expected_source_hits']}/{value['queries']} ({value['recall']:.1%})")
        summary.append(f"ANY_EXPECTED_SOURCE_AT_{k}={value['any_expected_source_hits']}/{value['queries']} ({value['any_source_recall']:.1%})")
    for name, values in category_scores.items():
        value = values["10"]
        summary.append(f"{name.upper()}_RECALL_AT_10={value['all_expected_source_hits']}/{value['queries']} ({value['recall']:.1%})")
    summary.extend([f"WRONG_REVISION_HITS={wrong_revision_count}", f"EXPLICIT_FILTER_LEAKS={filter_leaks}",
        f"UNRESOLVABLE_TOP10_LOCATORS={locator_errors}", f"REEMBED_SECONDS={reembed_seconds:.2f}",
        f"COLLECTION_POINTS={collection_info.points_count}", f"PHASE_PASS={phase_pass}", f"DECISION={decision}"])
    (evidence / "retrieval_summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print(json.dumps({"complete": True, "overall": scores["10"], "categories": {k: v["10"] for k,v in category_scores.items()}, "gate": final["gate"]}, ensure_ascii=False), flush=True)
    return 0 if phase_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
