from __future__ import annotations

import gc
import hashlib
import json
import os
import resource
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUN = Path(os.getenv("RAG391_RUN_DIR", "/Users/xiamin/dev/存储/public-knowledge-rag-391-run"))
sys.path.insert(0, str(REPO))

from public_knowledge_rag.tech_selection import (
    DoclingParser,
    HybridRRFIndex,
    OllamaEmbeddingProvider,
    QdrantDenseIndex,
    SQLiteFTS5Index,
    StructureAwareChunker,
)


OLLAMA_URL = os.getenv("OLLAMA_URL", "http://192.168.1.100:11434").rstrip("/")
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6339")
COLLECTION = "public_knowledge_rag_391"
EMBEDDING_MODEL = "qwen3-embedding:0.6b"
EMBEDDING_DIGEST = "ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports KiB.
    return int(value if sys.platform == "darwin" else value * 1024)


def classify_source(source: dict[str, str]) -> str:
    source_id = source["source_id"].upper()
    title = source["title"].upper()
    if "NVMEXPRESS-BASE" in source_id or "BASE SPECIFICATION" in title:
        return "standard"
    if any(token in source_id for token in ("NVMECLI", "LINUX-", "SQLITE-", "LITTLEFS-")):
        return "software"
    return "datasheet"


def source_path(source: dict[str, str]) -> Path:
    original = Path(source["path"])
    return RUN / "sources" / original.parent.name / original.name


def main() -> int:
    started = time.monotonic()
    evidence = RUN / "evidence"
    data = RUN / "data"
    parsed_dir = RUN / "parsed"
    evidence.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    parsed_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((RUN / "source_manifest.json").read_text(encoding="utf-8"))
    queries_data = json.loads((RUN / "golden_queries.json").read_text(encoding="utf-8"))
    sources = manifest["sources"]
    if len(sources) != 21 or len(queries_data["queries"]) != 60:
        raise SystemExit("Frozen input count mismatch; refusing evaluation")

    frozen_source_checks = []
    for source in sources:
        path = source_path(source)
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        expected = source.get("expected_content_sha256") or source.get("actual_download_sha256")
        if digest != expected:
            raise SystemExit(f"Frozen source SHA mismatch: {source['material_id']} expected={expected} actual={digest}")
        git_blob_sha = source.get("git_blob_sha1_expected")
        actual_git_blob_sha = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest() if git_blob_sha else None
        if git_blob_sha and git_blob_sha != actual_git_blob_sha:
            raise SystemExit(f"Frozen Git blob mismatch: {source['material_id']} expected={git_blob_sha} actual={actual_git_blob_sha}")
        frozen_source_checks.append({"material_id": source["material_id"], "path": str(path.relative_to(RUN)), "sha256": digest, "expected_sha256": expected, "git_blob_sha1_expected": git_blob_sha, "git_blob_sha1_actual": actual_git_blob_sha, "match": True})
    write_json(evidence / "frozen_input_checks.json", {"sources": frozen_source_checks, "source_count": len(sources), "query_count": len(queries_data["queries"])})

    parser = DoclingParser()
    chunker = StructureAwareChunker(target_tokens=800)
    records: list[dict[str, str]] = []
    chunk_token_counts: list[int] = []
    source_rows = []
    for index, source in enumerate(sources, start=1):
        path = source_path(source)
        parsed = parser.parse(path.read_bytes(), source["media_type"])
        chunks = chunker.chunk(parsed)
        source_id = source["source_id"]
        revision = source["source_revision_id"]
        source_chunk_counts = []
        for chunk in chunks:
            cid = f"{source_id}::{revision}::{chunk.ordinal}"
            token_count = chunker.token_count(chunk.text)
            chunk_token_counts.append(token_count)
            source_chunk_counts.append(token_count)
            records.append({
                "chunk_id": cid,
                "source_id": source_id,
                "source_revision": revision,
                "locator": chunk.locator,
                "title": source["title"],
                "text": chunk.text,
            })
        source_rows.append({
            "material_id": source["material_id"],
            "source_id": source_id,
            "source_revision": revision,
            "title": source["title"],
            "media_type": source["media_type"],
            "source_class": classify_source(source),
            "page_count": parsed.page_count,
            "parsed_elements": len(parsed.elements),
            "chunk_count": len(chunks),
            "chunk_tokens_min": min(source_chunk_counts) if source_chunk_counts else 0,
            "chunk_tokens_max": max(source_chunk_counts) if source_chunk_counts else 0,
        })
        if index % 3 == 0 or index == len(sources):
            write_json(evidence / "chunking_progress.json", {
                "completed_sources": index,
                "total_sources": len(sources),
                "chunk_count": len(records),
                "max_rss_bytes": rss_bytes(),
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "source_rows": source_rows,
            })
            print(json.dumps({"phase": "chunk", "source": index, "sources": len(sources), "chunks": len(records), "rss_bytes": rss_bytes()}), flush=True)
        del parsed
        gc.collect()

    token_sorted = sorted(chunk_token_counts)
    def percentile(p: float) -> int:
        if not token_sorted:
            return 0
        return token_sorted[min(len(token_sorted) - 1, int((len(token_sorted) - 1) * p))]

    chunking = {
        "chunker": "Docling HybridChunker 2.133.0",
        "tokenizer": "sentence-transformers/all-MiniLM-L6-v2 (Docling cached default; Qwen GGUF exposes tokenizer model=qwen3/qwen2 but not the vocab; HF tokenizer fetch timed out)",
        "target_tokens": 800,
        "merge_peers": True,
        "repeat_table_header": True,
        "overlap_tokens": "not applied by HybridChunker",
        "source_count": len(sources),
        "chunk_count": len(records),
        "p50_tokens": percentile(0.50),
        "p90_tokens": percentile(0.90),
        "max_tokens": max(chunk_token_counts, default=0),
        "token_count_basis": "HybridChunker contextualized serialization",
        "sources": source_rows,
        "max_rss_bytes_after_parse_and_chunk": rss_bytes(),
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }
    write_json(evidence / "chunking_summary.json", chunking)
    # Preserve chunk text/locators as reviewable Phase 1 evidence.
    write_json(parsed_dir / "hybrid_chunks.json", records)

    db_path = data / "chunks.sqlite3"
    if db_path.exists():
        raise SystemExit(f"Refusing to overwrite non-fresh SQLite index: {db_path}")
    lexical = SQLiteFTS5Index(db_path)
    dense = QdrantDenseIndex(QDRANT_URL, COLLECTION, OllamaEmbeddingProvider(timeout=300), 1024)
    metadata = [{
        "material_id": row["material_id"],
        "source_id": row["source_id"],
        "source_revision": row["source_revision"],
        "title": row["title"],
        "media_type": row["media_type"],
        "source_class": row["source_class"],
    } for row in source_rows]
    retriever = HybridRRFIndex(dense, lexical, rrf_k=60, dense_candidates=20, lexical_candidates=20, source_metadata=metadata)

    batch_size = 16
    import_rows = []
    for start in range(0, len(records), batch_size):
        batch = records[start:start + batch_size]
        vectors = dense.embedding.embed([record["text"] for record in batch])
        dense.upsert(batch, vectors)
        lexical.upsert(batch)
        import_rows.append({"start": start, "count": len(batch), "dimensions": len(vectors[0])})
        if (len(import_rows) % 10 == 0) or start + len(batch) == len(records):
            write_json(evidence / "index_progress.json", {
                "completed_records": start + len(batch),
                "total_records": len(records),
                "batch_size": batch_size,
                "batch_count": len(import_rows),
                "max_rss_bytes": rss_bytes(),
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "batches": import_rows,
            })
            print(json.dumps({"phase": "index", "records": start + len(batch), "total": len(records), "rss_bytes": rss_bytes()}), flush=True)

    source_by_material = {row["material_id"]: row for row in source_rows}
    source_revision_by_id = {row["source_id"]: row["source_revision"] for row in source_rows}
    rows = []
    for position, query in enumerate(queries_data["queries"], start=1):
        hits = retriever.search(query["query"], 10)
        expected_ids = set(query["source_ids"])
        top_ids = {hit.source_id for hit in hits}
        hit_expected = expected_ids.issubset(top_ids)
        wrong_source_top1 = bool(hits and hits[0].source_id not in expected_ids)
        expected_classes = {source_by_material[mid]["source_class"] for mid in query["source_materials"]}
        wrong_revision = [hit.hit_id for hit in hits if source_revision_by_id.get(hit.source_id) != hit.source_revision]
        locator_errors = []
        for hit in hits:
            try:
                loc = json.loads(hit.locator)
                if not isinstance(loc, dict) or not (loc.get("page_start") or loc.get("element_refs")):
                    locator_errors.append(hit.hit_id)
            except Exception:
                locator_errors.append(hit.hit_id)
        filter_evidence = retriever.last_search_filter
        wrong_explicit_source = []
        if filter_evidence.get("applied"):
            allowed_ids = set(filter_evidence["source_ids"])
            wrong_explicit_source = [hit.hit_id for hit in hits if hit.source_id not in allowed_ids]
        rows.append({
            "id": query["id"],
            "query": query["query"],
            "expected_materials": query["source_materials"],
            "expected_source_ids": query["source_ids"],
            "expected_source_classes": sorted(expected_classes),
            "recall_hit_at_10": hit_expected,
            "wrong_source_top1": wrong_source_top1,
            "metadata_filter": filter_evidence,
            "wrong_explicit_source_hits": wrong_explicit_source,
            "wrong_revision_hits": wrong_revision,
            "unresolvable_locators": locator_errors,
            "top10": [{
                "hit_id": hit.hit_id,
                "source_id": hit.source_id,
                "source_revision": hit.source_revision,
                "locator": hit.locator,
                "score": hit.score,
                "text": hit.text,
            } for hit in hits],
        })
        if position % 10 == 0:
            write_json(evidence / "retrieval_progress.json", {"completed": position, "total": len(queries_data["queries"]), "rows": rows})
            print(json.dumps({"phase": "retrieval", "completed": position, "total": len(queries_data["queries"]), "elapsed_seconds": round(time.monotonic() - started, 2)}), flush=True)

    def recall(selected: list[dict[str, object]]) -> dict[str, object]:
        count = len(selected)
        hits = sum(1 for row in selected if row["recall_hit_at_10"])
        return {"hits": hits, "queries": count, "recall": hits / count if count else None}

    category_scores = {}
    for category in ("datasheet", "standard", "software"):
        category_scores[category] = recall([row for row in rows if category in row["expected_source_classes"]])
    all_score = recall(rows)
    wrong_sources = sum(bool(row["wrong_source_top1"]) for row in rows)
    explicit_filter_leaks = sum(len(row["wrong_explicit_source_hits"]) for row in rows)
    wrong_revisions = sum(len(row["wrong_revision_hits"]) for row in rows)
    unresolved_locators = sum(len(row["unresolvable_locators"]) for row in rows)
    collection_state = dense.client.get_collection(COLLECTION)
    report = {
        "task": "PUBLIC-KNOWLEDGE-RAG-COMPONENT-RESELECT-001",
        "phase": "PHASE1_RETRIEVAL_ONLY",
        "generation_calls": 0,
        "ollama_url": OLLAMA_URL,
        "qdrant_url": QDRANT_URL,
        "collection": COLLECTION,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_digest": EMBEDDING_DIGEST,
        "embedding_query_instruction": OllamaEmbeddingProvider.QUERY_INSTRUCTION,
        "candidate_depths": {"dense": 20, "lexical": 20, "rrf_k": 60, "top_k": 10},
        "all": all_score,
        "categories": category_scores,
        "wrong_source_top1": wrong_sources,
        "explicit_filter_leaks": explicit_filter_leaks,
        "wrong_revision": wrong_revisions,
        "unresolvable_top10_locators": unresolved_locators,
        "source_count": len(sources),
        "query_count": len(rows),
        "chunking": chunking,
        "index": {
            "sqlite_path": str(db_path),
            "sqlite_rows": lexical.connect().execute("select count(*) from chunks_fts").fetchone()[0],
            "qdrant_points": collection_state.points_count,
            "qdrant_status": str(collection_state.status),
            "sqlite_bytes": db_path.stat().st_size,
            "process_max_rss_bytes": rss_bytes(),
            "elapsed_seconds": round(time.monotonic() - started, 2),
        },
        "gate": {
            "overall_recall_at_10_ge_90": bool(all_score["recall"] is not None and all_score["recall"] >= 0.9),
            "category_recalls_ge_90": all(score["recall"] is not None and score["recall"] >= 0.9 for score in category_scores.values()),
            "wrong_source_zero": wrong_sources == 0,
            "explicit_filter_leaks_zero": explicit_filter_leaks == 0,
            "wrong_revision_zero": wrong_revisions == 0,
            "phase1_pass": bool(
                all_score["recall"] is not None and all_score["recall"] >= 0.9
                and all(score["recall"] is not None and score["recall"] >= 0.9 for score in category_scores.values())
                and wrong_sources == 0 and wrong_revisions == 0
            ),
        },
        "rows": rows,
    }
    write_json(evidence / "retrieval_results.json", report)
    (evidence / "retrieval_summary.txt").write_text(
        "PHASE1_RETRIEVAL_ONLY\n"
        f"RECALL_AT_10={all_score['hits']}/{all_score['queries']} ({all_score['recall']:.1%})\n"
        + "\n".join(f"{category.upper()}_RECALL_AT_10={score['hits']}/{score['queries']} ({score['recall']:.1%})" for category, score in category_scores.items())
        + f"\nWRONG_SOURCE_TOP1={wrong_sources}\nWRONG_REVISION={wrong_revisions}\nEXPLICIT_FILTER_LEAKS={explicit_filter_leaks}\nUNRESOLVABLE_TOP10_LOCATORS={unresolved_locators}\nGENERATION_CALLS=0\nPHASE1_PASS={report['gate']['phase1_pass']}\n",
        encoding="utf-8",
    )
    print(json.dumps({"phase": "complete", "gate": report["gate"], "recall": all_score, "categories": category_scores, "wrong_source_top1": wrong_sources, "wrong_revision": wrong_revisions, "explicit_filter_leaks": explicit_filter_leaks, "rss_bytes": rss_bytes(), "elapsed_seconds": round(time.monotonic() - started, 2)}, ensure_ascii=False), flush=True)
    return 0 if report["gate"]["phase1_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
