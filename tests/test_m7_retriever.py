from pathlib import Path
import json

from builder.embedding_client import LocalHashEmbeddingClient
from builder.m7_runner import run_m7


ROOT = Path(__file__).resolve().parents[1]


def test_m7_retrieves_similar_case(tmp_path, monkeypatch):
    app_path = ROOT / "config" / "app.yaml"
    app_text = app_path.read_text(encoding="utf-8")

    index_dir = ROOT / "knowledge" / "index"
    docs_dir = ROOT / "knowledge" / "retrieval_docs"
    embeddings_dir = ROOT / "knowledge" / "embeddings"
    for directory in [index_dir, docs_dir, embeddings_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    client = LocalHashEmbeddingClient(dimensions=256, model="local-hash-v1")
    samples = [
        ("CASE-000001", "CAN接收拥堵导致软件保护重启", "传动IPMT", "低压变频器SPDT"),
        ("CASE-000002", "界面按钮颜色显示异常", "控制IPMT", "HMI SPDT"),
    ]
    records = []
    created = []
    try:
        for case_id, text, ipmt, spdt in samples:
            doc_path = docs_dir / f"{case_id}.json"
            emb_path = embeddings_dir / f"{case_id}.json"
            doc = {
                "document_id": f"DOC-{case_id}",
                "case_id": case_id,
                "itr_id": "",
                "source_case_path": "",
                "organization": {
                    "ipmt": ipmt,
                    "spdt": spdt,
                    "responsible_department_level2": "",
                },
                "classification": {
                    "cause_level1": "软件设计",
                    "cause_level2": "资源处理",
                    "source": "AI",
                },
                "filters": {"product": "", "domain": ""},
                "title": text,
                "text": text,
                "tags": ["CAN", "拥堵"] if "CAN" in text else ["界面"],
                "quality_flags": [],
                "content_hash": case_id,
                "generated_at": "",
            }
            embedding = {
                "document_id": doc["document_id"],
                "case_id": case_id,
                "model": "local-hash-v1",
                "dimensions": 256,
                "vector": client.embed(text).vector,
                "content_hash": case_id,
                "generated_at": "",
            }
            doc_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            emb_path.write_text(json.dumps(embedding, ensure_ascii=False), encoding="utf-8")
            created.extend([doc_path, emb_path])
            records.append({
                "document_id": doc["document_id"],
                "case_id": case_id,
                "title": text,
                "organization": doc["organization"],
                "classification": doc["classification"],
                "filters": doc["filters"],
                "tags": doc["tags"],
                "quality_flags": [],
                "content_hash": case_id,
                "embedding_path": str(emb_path.relative_to(ROOT)),
                "retrieval_doc_path": str(doc_path.relative_to(ROOT)),
            })

        index_path = index_dir / "case_index.jsonl"
        index_path.write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in records) + "\n",
            encoding="utf-8",
        )
        created.append(index_path)

        result = run_m7(
            ROOT,
            query_text="CAN报文过多，接收处理拥堵后软件重启",
            ipmt="传动IPMT",
            spdt="低压变频器SPDT",
            top_k=2,
        )
        assert result["returned_count"] >= 1
        assert result["results"][0]["case_id"] == "CASE-000001"
        assert result["results"][0]["reasons"]
    finally:
        for path in created:
            path.unlink(missing_ok=True)
