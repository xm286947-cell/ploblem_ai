from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
import csv
import json

import yaml

from parser.common import write_json
from retriever.case_retriever import CaseRetriever, QueryInput


def load_yaml(path: str | Path) -> Dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _write_markdown(path: Path, result: dict) -> None:
    query = result["query"]
    lines = [
        "# 重复问题检索结果",
        "",
        "## 查询",
        "",
        query["text"],
        "",
        f"- IPMT：{query['organization']['ipmt'] or '未指定'}",
        f"- SPDT：{query['organization']['spdt'] or '未指定'}",
        f"- 原因描述：{query.get('cause_description') or '未提供'}",
        f"- 原因分类：{query['classification']['cause_level1'] or '未指定'} / {query['classification']['cause_level2'] or '未指定'}",
        f"- 解决措施：{query.get('solution') or '未提供'}",
        f"- 返回案例数：{result.get('returned_count', 0)}",
        "",
        "## 相似案例",
        "",
    ]
    for item in result.get("results", []):
        lines.extend([
            f"### {item['rank']}. {item['title']}",
            "",
            f"- Case ID：{item['case_id']}",
            f"- 综合得分：{item['score']:.4f}",
            f"- IPMT / SPDT：{item['organization'].get('ipmt','')} / {item['organization'].get('spdt','')}",
            f"- 原因分类：{item['classification'].get('cause_level1','')} / {item['classification'].get('cause_level2','')}",
            f"- 问题得分：{item['score_breakdown'].get('problem',0):.4f}",
            f"- 原因得分：{item['score_breakdown'].get('cause',0):.4f}",
            f"- 分类得分：{item['score_breakdown'].get('classification',0):.4f}",
            f"- 措施得分：{item['score_breakdown'].get('solution',0):.4f}",
            f"- 组织得分：{item['score_breakdown'].get('organization',0):.4f}",
            f"- 推荐理由：{'；'.join(item['reasons'])}",
            "",
        ])
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_csv(path: Path, result: dict) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=[
            "rank", "case_id", "title", "score", "ipmt", "spdt",
            "cause_level1", "cause_level2", "problem_score", "cause_score",
            "classification_score", "solution_score", "organization_score", "reasons"
        ])
        writer.writeheader()
        for item in result.get("results", []):
            writer.writerow({
                "rank": item["rank"],
                "case_id": item["case_id"],
                "title": item["title"],
                "score": item["score"],
                "ipmt": item["organization"].get("ipmt", ""),
                "spdt": item["organization"].get("spdt", ""),
                "cause_level1": item["classification"].get("cause_level1", ""),
                "cause_level2": item["classification"].get("cause_level2", ""),
                "problem_score": item["score_breakdown"].get("problem", 0),
                "cause_score": item["score_breakdown"].get("cause", 0),
                "classification_score": item["score_breakdown"].get("classification", 0),
                "solution_score": item["score_breakdown"].get("solution", 0),
                "organization_score": item["score_breakdown"].get("organization", 0),
                "reasons": "；".join(item["reasons"]),
            })


def run_m7(
    project_root: str | Path,
    query_text: str,
    top_k: int | None = None,
    ipmt: str = "",
    spdt: str = "",
    responsible_department_level2: str = "",
    product: str = "",
    domain: str = "",
    cause_level1: str = "",
    cause_level2: str = "",
    cause_description: str = "",
    solution: str = "",
) -> dict:
    root = Path(project_root).resolve()
    app = load_yaml(root / "config/app.yaml")
    model = load_yaml(root / "config/model.yaml")
    retrieval_config = load_yaml(root / "config/retrieval.yaml")
    output_dir = root / app["paths"]["retrieval_output_dir"]
    logs_dir = root / app["paths"]["logs_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    query = QueryInput(
        text=query_text,
        ipmt=ipmt,
        spdt=spdt,
        responsible_department_level2=responsible_department_level2,
        product=product,
        domain=domain,
        cause_level1=cause_level1,
        cause_level2=cause_level2,
        cause_description=cause_description,
        solution=solution,
    )
    retriever = CaseRetriever(root, app, model, retrieval_config)
    result = retriever.search(query, top_k=top_k)
    result["stage"] = "M7"
    result["generated_at"] = datetime.now(timezone.utc).isoformat()

    write_json(output_dir / "query.json", query.to_dict())
    write_json(output_dir / "retrieval_result.json", result)
    _write_markdown(output_dir / "explanation.md", result)
    _write_csv(output_dir / "top_cases.csv", result)
    write_json(logs_dir / "m7_summary.json", {
        "stage": "M7",
        "returned_count": result.get("returned_count", 0),
        "total_candidates": result.get("total_candidates", 0),
        "embedding_model": result.get("embedding_model", ""),
        "output_dir": str(output_dir),
    })
    return result
