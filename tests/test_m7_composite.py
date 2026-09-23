from pathlib import Path
import json

from builder.embedding_client import LocalHashEmbeddingClient
from builder.m7_runner import run_m7

ROOT = Path(__file__).resolve().parents[1]


def test_cause_classification_and_solution_change_ranking():
    index_dir = ROOT / "knowledge" / "index"
    docs_dir = ROOT / "knowledge" / "retrieval_docs"
    embeddings_dir = ROOT / "knowledge" / "embeddings"
    enriched_dir = ROOT / "knowledge" / "enriched_case"
    for directory in [index_dir, docs_dir, embeddings_dir, enriched_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    client = LocalHashEmbeddingClient(dimensions=256, model="local-hash-v1")
    created = []
    records = []

    samples = [
        {
            "case_id": "CASE-A",
            "title": "设备运行中软件异常重启",
            "cause1": "软件设计",
            "cause2": "队列拥堵处理",
            "cause": "CAN接收队列缺少流量控制，报文堆积导致任务阻塞",
            "solution": "增加队列水位监控和报文限流，优化接收任务调度",
        },
        {
            "case_id": "CASE-B",
            "title": "设备运行中软件异常重启",
            "cause1": "硬件问题",
            "cause2": "电源异常",
            "cause": "电源瞬时跌落触发复位",
            "solution": "更换电源模块并增加欠压保护",
        },
    ]

    try:
        for item in samples:
            case_id = item["case_id"]
            enriched_path = enriched_dir / f"{case_id}.json"
            enriched = {
                "metadata": {"case_id": case_id, "itr_id": ""},
                "business_context": {
                    "ipmt": "传动IPMT",
                    "spdt": "低压变频器SPDT",
                    "responsible_department_level2": "",
                    "product": "",
                    "domain": "",
                },
                "problem": {
                    "standard_description": item["title"],
                    "report_description": "",
                    "original_description": item["title"],
                },
                "analysis": {
                    "trc": {
                        "occurrence": {"standard": item["cause"], "report": "", "original": ""},
                        "escape": {"standard": "", "report": "", "original": ""},
                    },
                    "mrc": {
                        "occurrence": {"standard": "", "report": "", "original": ""},
                        "escape": {"standard": "", "report": "", "original": ""},
                    },
                    "root_cause": [{"value": item["cause"]}],
                    "failure_mechanism": [],
                },
                "solution": {
                    "corrective_actions": [{"value": item["solution"]}],
                    "preventive_actions": [],
                    "reusable_actions": [],
                },
                "knowledge": {"normalized_problem": item["title"]},
            }
            enriched_path.write_text(json.dumps(enriched, ensure_ascii=False), encoding="utf-8")
            created.append(enriched_path)

            doc_path = docs_dir / f"{case_id}.json"
            emb_path = embeddings_dir / f"{case_id}.json"
            doc = {
                "document_id": f"DOC-{case_id}",
                "case_id": case_id,
                "itr_id": "",
                "source_case_path": str(enriched_path.relative_to(ROOT)),
                "organization": {
                    "ipmt": "传动IPMT",
                    "spdt": "低压变频器SPDT",
                    "responsible_department_level2": "",
                },
                "classification": {
                    "cause_level1": item["cause1"],
                    "cause_level2": item["cause2"],
                    "source": "AI",
                },
                "filters": {"product": "", "domain": ""},
                "title": item["title"],
                "text": item["title"],
                "tags": [],
                "quality_flags": [],
                "content_hash": case_id,
                "generated_at": "",
            }
            doc_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            emb_path.write_text(json.dumps({
                "document_id": doc["document_id"],
                "case_id": case_id,
                "model": "local-hash-v1",
                "dimensions": 256,
                "vector": client.embed(item["title"]).vector,
                "content_hash": case_id,
                "generated_at": "",
            }, ensure_ascii=False), encoding="utf-8")
            created.extend([doc_path, emb_path])
            records.append({
                "document_id": doc["document_id"],
                "case_id": case_id,
                "title": doc["title"],
                "organization": doc["organization"],
                "classification": doc["classification"],
                "filters": doc["filters"],
                "tags": [],
                "quality_flags": [],
                "content_hash": case_id,
                "embedding_path": str(emb_path.relative_to(ROOT)),
                "retrieval_doc_path": str(doc_path.relative_to(ROOT)),
            })

        index_path = index_dir / "case_index.jsonl"
        index_path.write_text(
            "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
            encoding="utf-8",
        )
        created.append(index_path)

        result = run_m7(
            ROOT,
            query_text="设备运行中软件异常重启",
            cause_description="CAN报文堆积造成接收队列拥堵和任务阻塞",
            cause_level1="软件设计",
            cause_level2="队列拥堵处理",
            solution="增加报文限流和队列水位监控",
            ipmt="传动IPMT",
            spdt="低压变频器SPDT",
            top_k=2,
        )
        assert result["results"][0]["case_id"] == "CASE-A"
        assert result["results"][0]["score_breakdown"]["cause"] > result["results"][1]["score_breakdown"]["cause"]
        assert result["results"][0]["score_breakdown"]["solution"] > result["results"][1]["score_breakdown"]["solution"]
        assert result["results"][0]["score_breakdown"]["classification"] == 1.0
    finally:
        for path in created:
            path.unlink(missing_ok=True)
