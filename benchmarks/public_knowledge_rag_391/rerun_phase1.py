from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUN = Path(os.getenv("RAG391_RUN_DIR", "/Users/xiamin/dev/存储/public-knowledge-rag-391-run"))
sys.path.insert(0, str(REPO))

from public_knowledge_rag.tech_selection import HybridRRFIndex, OllamaEmbeddingProvider, QdrantDenseIndex, SQLiteFTS5Index


def main() -> int:
    evidence = RUN / "evidence"
    manifest = json.loads((RUN / "source_manifest.json").read_text(encoding="utf-8"))
    queries = json.loads((RUN / "golden_queries.json").read_text(encoding="utf-8"))["queries"]
    chunking = json.loads((evidence / "chunking_summary.json").read_text(encoding="utf-8"))
    source_rows = chunking["sources"]
    row_by_material = {r["material_id"]: r for r in source_rows}
    chunks = json.loads((RUN / "parsed/hybrid_chunks.json").read_text(encoding="utf-8"))
    chunk_by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    source_by_id = {row["source_id"]: row for row in source_rows}
    source_metadata = [{
        "material_id": row["material_id"],
        "source_id": row["source_id"],
        "source_revision": row["source_revision"],
        "title": row["title"],
        "media_type": row["media_type"],
        "source_class": row["source_class"],
    } for row in source_rows]
    db_path = RUN / "data/chunks.sqlite3"
    lexical = SQLiteFTS5Index(db_path)
    dense = QdrantDenseIndex(
        os.getenv("QDRANT_URL", "http://127.0.0.1:6339"),
        "public_knowledge_rag_391",
        OllamaEmbeddingProvider(timeout=300),
        1024,
    )
    retriever = HybridRRFIndex(dense, lexical, 60, 20, 20, source_metadata)
    revision_by_id = {r["source_id"]: r["source_revision"] for r in source_rows}
    rows = []
    started = time.monotonic()
    for index, query in enumerate(queries, start=1):
        hits = retriever.search(query["query"], 10)
        expected_ids = set(query["source_ids"])
        top_ids = {hit.source_id for hit in hits}
        expected_classes = {row_by_material[mid]["source_class"] for mid in query["source_materials"]}
        wrong_revision = [hit.hit_id for hit in hits if revision_by_id.get(hit.source_id) != hit.source_revision]
        wrong_source_top1 = bool(hits and hits[0].source_id not in expected_ids)
        locators = []
        for hit in hits:
            try:
                locator = json.loads(hit.locator)
                indexed_chunk = chunk_by_id.get(hit.hit_id)
                source = source_by_id.get(hit.source_id, {})
                refs = locator.get("element_refs", []) if isinstance(locator, dict) else []
                page_start = locator.get("page_start") if isinstance(locator, dict) else None
                page_end = locator.get("page_end") if isinstance(locator, dict) else None
                resolves_to_chunk = bool(
                    indexed_chunk
                    and indexed_chunk["source_id"] == hit.source_id
                    and indexed_chunk["source_revision"] == hit.source_revision
                    and indexed_chunk["locator"] == hit.locator
                )
                resolves_to_location = bool(refs) or bool(
                    page_start and page_end and page_start >= 1 and page_end >= page_start
                    and page_end <= source.get("page_count", 0)
                )
                if not resolves_to_chunk or not resolves_to_location:
                    locators.append(hit.hit_id)
            except Exception:
                locators.append(hit.hit_id)
        filter_evidence = retriever.last_search_filter
        wrong_explicit_source = []
        if filter_evidence.get("applied"):
            allowed = set(filter_evidence["source_ids"])
            wrong_explicit_source = [hit.hit_id for hit in hits if hit.source_id not in allowed]
        rows.append({
            "id": query["id"],
            "query": query["query"],
            "expected_materials": query["source_materials"],
            "expected_source_ids": query["source_ids"],
            "expected_source_classes": sorted(expected_classes),
            "recall_hit_at_10": expected_ids.issubset(top_ids),
            "wrong_source_top1": wrong_source_top1,
            "metadata_filter": filter_evidence,
            "wrong_explicit_source_hits": wrong_explicit_source,
            "wrong_revision_hits": wrong_revision,
            "unresolvable_locators": locators,
            "top10": [{
                "hit_id": hit.hit_id,
                "source_id": hit.source_id,
                "source_revision": hit.source_revision,
                "locator": hit.locator,
                "score": hit.score,
                "text": hit.text,
            } for hit in hits],
        })
        if index % 10 == 0:
            print(json.dumps({"phase": "retrieval-corrected", "completed": index, "total": len(queries), "elapsed_seconds": round(time.monotonic() - started, 2)}), flush=True)

    def score(selected):
        hits = sum(1 for r in selected if r["recall_hit_at_10"])
        return {"hits": hits, "queries": len(selected), "recall": hits / len(selected) if selected else None}

    categories = {name: score([r for r in rows if name in r["expected_source_classes"]]) for name in ("datasheet", "standard", "software")}
    overall = score(rows)
    wrong_source = sum(bool(r["wrong_source_top1"]) for r in rows)
    explicit_filter_leaks = sum(len(r["wrong_explicit_source_hits"]) for r in rows)
    wrong_revision = sum(len(r["wrong_revision_hits"]) for r in rows)
    unresolved = sum(len(r["unresolvable_locators"]) for r in rows)
    filters_applied = sum(bool(r["metadata_filter"].get("applied")) for r in rows)
    collection = dense.client.get_collection("public_knowledge_rag_391")
    gate = bool(
        overall["recall"] >= 0.9
        and all(s["recall"] is not None and s["recall"] >= 0.9 for s in categories.values())
        and wrong_source == 0 and wrong_revision == 0
    )
    result = {
        "task": "PUBLIC-KNOWLEDGE-RAG-COMPONENT-RESELECT-001",
        "phase": "PHASE1_RETRIEVAL_ONLY_CORRECTED_GENERIC_ENTITY_FILTER",
        "generation_calls": 0,
        "embedding_model": "qwen3-embedding:0.6b",
        "embedding_digest": "ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d",
        "embedding_query_instruction": OllamaEmbeddingProvider.QUERY_INSTRUCTION,
        "candidate_depths": {"dense": 20, "lexical": 20, "rrf_k": 60, "top_k": 10},
        "overall": overall,
        "categories": categories,
        "wrong_source_top1": wrong_source,
        "explicit_filter_leaks": explicit_filter_leaks,
        "wrong_revision": wrong_revision,
        "unresolvable_top10_locators": unresolved,
        "filters_applied": filters_applied,
        "chunking": chunking,
        "index": {"sqlite_path": str(db_path), "sqlite_rows": lexical.connect().execute("select count(*) from chunks_fts").fetchone()[0], "qdrant_points": collection.points_count, "qdrant_status": str(collection.status)},
        "gate": {"overall_recall_at_10_ge_90": overall["recall"] >= 0.9, "category_recalls_ge_90": all(s["recall"] is not None and s["recall"] >= 0.9 for s in categories.values()), "wrong_source_zero": wrong_source == 0, "wrong_revision_zero": wrong_revision == 0, "explicit_filter_leaks_zero": explicit_filter_leaks == 0, "phase1_pass": gate},
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "rows": rows,
    }
    (evidence / "retrieval_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (evidence / "retrieval_summary.txt").write_text(
        "PHASE1_RETRIEVAL_ONLY_CORRECTED_GENERIC_ENTITY_FILTER\n"
        f"RECALL_AT_10={overall['hits']}/{overall['queries']} ({overall['recall']:.1%})\n"
        + "\n".join(f"{name.upper()}_RECALL_AT_10={entry['hits']}/{entry['queries']} ({entry['recall']:.1%})" for name, entry in categories.items())
        + f"\nWRONG_SOURCE_TOP1={wrong_source}\nWRONG_REVISION={wrong_revision}\nEXPLICIT_FILTER_LEAKS={explicit_filter_leaks}\nUNRESOLVABLE_TOP10_LOCATORS={unresolved}\nFILTERS_APPLIED={filters_applied}\nGENERATION_CALLS=0\nPHASE1_PASS={gate}\n",
        encoding="utf-8",
    )
    print(json.dumps({"complete": True, "recall": overall, "categories": categories, "wrong_source_top1": wrong_source, "wrong_revision": wrong_revision, "explicit_filter_leaks": explicit_filter_leaks, "filters_applied": filters_applied, "gate": gate}, ensure_ascii=False), flush=True)
    return 0 if gate else 2


if __name__ == "__main__":
    raise SystemExit(main())
