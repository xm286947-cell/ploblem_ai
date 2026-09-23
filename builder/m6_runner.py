from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
import json

import yaml

from builder.embedding_client import create_embedding_client
from builder.retrieval_document_builder import RetrievalDocumentBuilder
from builder.validators import validate_json
from parser.common import write_json


def load_yaml(path: str | Path) -> Dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def run_m6(
    project_root: str | Path,
    case_id: str | None = None,
    overwrite: bool = False,
) -> dict:
    root = Path(project_root).resolve()
    app = load_yaml(root / "config/app.yaml")
    model = load_yaml(root / "config/model.yaml")
    paths = app["paths"]

    enriched_dir = root / paths["enriched_case_dir"]
    retrieval_docs_dir = root / paths["retrieval_docs_dir"]
    embeddings_dir = root / paths["embeddings_dir"]
    index_dir = root / paths["index_dir"]
    manifests_dir = root / paths["manifests_dir"]
    logs_dir = root / paths["logs_dir"]

    for directory in [retrieval_docs_dir, embeddings_dir, index_dir, manifests_dir, logs_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    source_files = sorted(enriched_dir.glob("*.json"))
    if case_id:
        source_files = [enriched_dir / f"{case_id}.json"]

    retrieval_schema = root / "schema/retrieval_document.schema.json"
    embedding_schema = root / "schema/embedding_record.schema.json"
    manifest_schema = root / "schema/knowledge_manifest.schema.json"

    builder = RetrievalDocumentBuilder()
    client = create_embedding_client(model["embedding"])

    results: List[dict] = []
    failures: List[dict] = []
    manifest_items: List[dict] = []
    index_records: List[dict] = []

    for source_path in source_files:
        current_case_id = source_path.stem
        if not source_path.exists():
            failures.append({"case_id": current_case_id, "error": "ENRICHED_CASE_NOT_FOUND"})
            continue

        retrieval_path = retrieval_docs_dir / source_path.name
        embedding_path = embeddings_dir / source_path.name

        if retrieval_path.exists() and embedding_path.exists() and not overwrite:
            try:
                retrieval_doc = json.loads(retrieval_path.read_text(encoding="utf-8"))
                embedding = json.loads(embedding_path.read_text(encoding="utf-8"))
                manifest_items.append({
                    "document_id": retrieval_doc["document_id"],
                    "case_id": current_case_id,
                    "retrieval_doc_path": str(retrieval_path.relative_to(root)),
                    "embedding_path": str(embedding_path.relative_to(root)),
                    "content_hash": retrieval_doc["content_hash"],
                })
                index_records.append({
                    "document_id": retrieval_doc["document_id"],
                    "case_id": current_case_id,
                    "title": retrieval_doc["title"],
                    "organization": retrieval_doc["organization"],
                    "classification": retrieval_doc["classification"],
                    "filters": retrieval_doc["filters"],
                    "tags": retrieval_doc["tags"],
                    "quality_flags": retrieval_doc["quality_flags"],
                    "content_hash": retrieval_doc["content_hash"],
                    "embedding_path": str(embedding_path.relative_to(root)),
                    "retrieval_doc_path": str(retrieval_path.relative_to(root)),
                })
                results.append({"case_id": current_case_id, "status": "SKIPPED", "reason": "M6_OUTPUT_EXISTS"})
                continue
            except Exception:
                pass

        try:
            case = json.loads(source_path.read_text(encoding="utf-8"))
            retrieval_doc = builder.build(case, str(source_path.relative_to(root)))
            errors = validate_json(retrieval_doc, retrieval_schema)
            if errors:
                failures.append({"case_id": current_case_id, "error": "RETRIEVAL_DOC_SCHEMA_INVALID", "details": errors})
                continue

            embedding_response = client.embed(retrieval_doc["text"])
            embedding_record = {
                "document_id": retrieval_doc["document_id"],
                "case_id": current_case_id,
                "model": embedding_response.model,
                "dimensions": len(embedding_response.vector),
                "vector": embedding_response.vector,
                "content_hash": retrieval_doc["content_hash"],
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
            errors = validate_json(embedding_record, embedding_schema)
            if errors:
                failures.append({"case_id": current_case_id, "error": "EMBEDDING_SCHEMA_INVALID", "details": errors})
                continue

            write_json(retrieval_path, retrieval_doc)
            write_json(embedding_path, embedding_record)

            manifest_items.append({
                "document_id": retrieval_doc["document_id"],
                "case_id": current_case_id,
                "retrieval_doc_path": str(retrieval_path.relative_to(root)),
                "embedding_path": str(embedding_path.relative_to(root)),
                "content_hash": retrieval_doc["content_hash"],
            })
            index_records.append({
                "document_id": retrieval_doc["document_id"],
                "case_id": current_case_id,
                "title": retrieval_doc["title"],
                "organization": retrieval_doc["organization"],
                "classification": retrieval_doc["classification"],
                "filters": retrieval_doc["filters"],
                "tags": retrieval_doc["tags"],
                "quality_flags": retrieval_doc["quality_flags"],
                "content_hash": retrieval_doc["content_hash"],
                "embedding_path": str(embedding_path.relative_to(root)),
                "retrieval_doc_path": str(retrieval_path.relative_to(root)),
            })
            results.append({
                "case_id": current_case_id,
                "status": "SUCCESS",
                "document_id": retrieval_doc["document_id"],
                "embedding_model": embedding_response.model,
                "dimensions": len(embedding_response.vector),
            })
        except Exception as exc:
            failures.append({
                "case_id": current_case_id,
                "error": str(exc),
                "error_type": type(exc).__name__,
            })

    now = datetime.now(timezone.utc).isoformat()
    dimensions = 0
    embedding_model = str(model["embedding"].get("model") or "")
    if manifest_items:
        sample = json.loads((root / manifest_items[0]["embedding_path"]).read_text(encoding="utf-8"))
        dimensions = int(sample["dimensions"])
        embedding_model = sample["model"]

    manifest = {
        "manifest_version": "1.0",
        "builder_version": app["app"]["builder_version"],
        "embedding_model": embedding_model,
        "dimensions": max(dimensions, 1),
        "document_count": len(manifest_items),
        "documents": sorted(manifest_items, key=lambda item: item["case_id"]),
        "generated_at": now,
    }
    manifest_errors = validate_json(manifest, manifest_schema)
    if manifest_errors:
        failures.append({"case_id": "_MANIFEST_", "error": "MANIFEST_SCHEMA_INVALID", "details": manifest_errors})
    else:
        write_json(manifests_dir / "knowledge_manifest.json", manifest)

    index_path = index_dir / "case_index.jsonl"
    with index_path.open("w", encoding="utf-8") as file:
        for record in sorted(index_records, key=lambda item: item["case_id"]):
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = {
        "stage": "M6",
        "total_cases": len(source_files),
        "success_count": sum(1 for item in results if item["status"] == "SUCCESS"),
        "skipped_count": sum(1 for item in results if item["status"] == "SKIPPED"),
        "failed_count": len(failures),
        "document_count": len(manifest_items),
        "embedding_model": embedding_model,
        "dimensions": dimensions,
        "results": results,
        "failures": failures,
    }
    write_json(logs_dir / "m6_summary.json", summary)
    write_json(logs_dir / "knowledge_build_failures.json", {"items": failures})
    return summary
