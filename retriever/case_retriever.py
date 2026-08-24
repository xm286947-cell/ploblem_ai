from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List
import json
import math
import re

from builder.embedding_client import create_embedding_client


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _tokens(text: str) -> set[str]:
    normalized = re.sub(r"\s+", " ", _text(text).lower())
    words = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]+", normalized))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    words.update(chinese[i:i + 2] for i in range(max(len(chinese) - 1, 0)))
    return {item for item in words if item}


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _values(items: Any) -> List[str]:
    result: List[str] = []
    for item in items or []:
        if isinstance(item, dict):
            value = _text(item.get("value"))
        else:
            value = _text(item)
        if value and value not in result:
            result.append(value)
    return result


def _first(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


@dataclass
class QueryInput:
    text: str
    cause_description: str = ""
    solution: str = ""
    ipmt: str = ""
    spdt: str = ""
    responsible_department_level2: str = ""
    product: str = ""
    domain: str = ""
    cause_level1: str = ""
    cause_level2: str = ""

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "cause_description": self.cause_description,
            "solution": self.solution,
            "organization": {
                "ipmt": self.ipmt,
                "spdt": self.spdt,
                "responsible_department_level2": self.responsible_department_level2,
            },
            "filters": {
                "product": self.product,
                "domain": self.domain,
            },
            "classification": {
                "cause_level1": self.cause_level1,
                "cause_level2": self.cause_level2,
            },
        }


class CaseRetriever:
    def __init__(self, root: Path, app: dict, model: dict, config: dict) -> None:
        self.root = root
        self.paths = app["paths"]
        self.config = config["retrieval"]
        self.embedding_client = create_embedding_client(model["embedding"])
        self.weights = self.config["weights"]
        self.org_weights = self.config["organization_weights"]

    def _load_index(self) -> List[dict]:
        path = self.root / self.paths["index_dir"] / "case_index.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"M6索引不存在: {path}")
        records = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not records:
            raise ValueError("M6索引为空，请先运行run-m6")
        return records

    def _load_document(self, record: dict) -> dict:
        return json.loads(
            (self.root / record["retrieval_doc_path"]).read_text(encoding="utf-8")
        )

    def _load_embedding(self, record: dict) -> dict:
        return json.loads(
            (self.root / record["embedding_path"]).read_text(encoding="utf-8")
        )

    def _load_case(self, document: dict) -> dict:
        source_path = _text(document.get("source_case_path"))
        if not source_path:
            return {}
        path = self.root / source_path
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _candidate_components(self, document: dict, case: dict) -> dict:
        problem = case.get("problem", {})
        analysis = case.get("analysis", {})
        solution = case.get("solution", {})
        knowledge = case.get("knowledge", {})

        problem_text = "\n".join(filter(None, [
            _text(knowledge.get("normalized_problem")),
            _text(problem.get("standard_description")),
            _text(problem.get("report_description")),
            _text(problem.get("original_description")),
            _text(document.get("title")),
        ]))

        cause_values: List[str] = []
        for group_name in ("trc", "mrc"):
            group = analysis.get(group_name, {})
            for kind in ("occurrence", "escape"):
                detail = group.get(kind, {})
                cause_values.append(_first(
                    detail.get("standard"),
                    detail.get("report"),
                    detail.get("original"),
                ))
        cause_values += _values(analysis.get("root_cause", []))
        cause_values += _values(analysis.get("failure_mechanism", []))
        cause_text = "\n".join(value for value in cause_values if value)

        solution_values = (
            _values(solution.get("corrective_actions", []))
            + _values(solution.get("preventive_actions", []))
            + _values(solution.get("reusable_actions", []))
        )
        solution_text = "\n".join(solution_values)

        # Compatibility fallback for older M6 outputs.
        if not cause_text:
            cause_text = _text(document.get("text"))
        if not solution_text:
            solution_text = _text(document.get("text"))

        return {
            "problem": problem_text,
            "cause": cause_text,
            "solution": solution_text,
        }

    def _hard_filter_match(self, query: QueryInput, record: dict) -> bool:
        filters = record.get("filters", {})
        # Hard filtering is intentionally limited to stable business boundaries.
        # Department and cause classification are low-trust fields and must never
        # eliminate a potentially relevant historical case.
        checks = [
            (query.product, filters.get("product")),
            (query.domain, filters.get("domain")),
        ]
        return all(not _text(expected) or _text(expected) == _text(actual) for expected, actual in checks)

    def _organization_score(self, query: QueryInput, record: dict) -> tuple[float, List[str]]:
        org = record.get("organization", {})
        values = [
            ("IPMT", query.ipmt, org.get("ipmt"), self.org_weights["ipmt"]),
            ("SPDT", query.spdt, org.get("spdt"), self.org_weights["spdt"]),
            ("责任部门", query.responsible_department_level2,
             org.get("responsible_department_level2"),
             self.org_weights["responsible_department_level2"]),
        ]
        available_weight = sum(weight for _, expected, _, weight in values if _text(expected))
        if available_weight == 0:
            return 0.0, []
        score = 0.0
        reasons = []
        for label, expected, actual, weight in values:
            if _text(expected) and _text(expected) == _text(actual):
                score += weight
                reasons.append(f"{label}一致")
        return score / available_weight, reasons

    def _classification_score(self, query: QueryInput, record: dict) -> tuple[float, List[str]]:
        classification = record.get("classification", {})
        pairs = [
            ("原因一级分类", query.cause_level1, classification.get("cause_level1")),
            ("原因二级分类", query.cause_level2, classification.get("cause_level2")),
        ]
        provided = [(label, expected, actual) for label, expected, actual in pairs if _text(expected)]
        if not provided:
            return 0.0, []
        matched = [
            label for label, expected, actual in provided
            if _text(expected) == _text(actual)
        ]
        return len(matched) / len(provided), [f"{label}一致" for label in matched]

    def _weighted_score(self, scores: dict, available: dict) -> tuple[float, dict]:
        active_weights = {
            key: float(self.weights.get(key, 0.0))
            for key, is_available in available.items()
            if is_available and float(self.weights.get(key, 0.0)) > 0
        }
        total_weight = sum(active_weights.values())
        if total_weight <= 0:
            return 0.0, {}
        normalized_weights = {
            key: weight / total_weight
            for key, weight in active_weights.items()
        }
        total = sum(normalized_weights[key] * scores.get(key, 0.0) for key in normalized_weights)
        return total, normalized_weights

    def search(self, query: QueryInput, top_k: int | None = None) -> dict:
        if not _text(query.text):
            raise ValueError("查询文本不能为空")

        records = self._load_index()
        hard_filter = str(self.config.get("filter_mode", "soft")).lower() == "hard"
        candidates = [
            item for item in records
            if not hard_filter or self._hard_filter_match(query, item)
        ]
        if not candidates:
            return {"query": query.to_dict(), "total_candidates": 0, "results": []}

        query_embedding = self.embedding_client.embed(query.text)
        problem_tokens = _tokens(query.text)
        cause_tokens = _tokens(query.cause_description)
        solution_tokens = _tokens(query.solution)
        scored = []

        for record in candidates:
            document = self._load_document(record)
            embedding = self._load_embedding(record)
            case = self._load_case(document)
            components = self._candidate_components(document, case)

            problem_vector = max(0.0, _cosine(query_embedding.vector, embedding["vector"]))
            problem_keyword = _jaccard(problem_tokens, _tokens(components["problem"]))
            problem_score = 0.75 * problem_vector + 0.25 * problem_keyword

            cause_score = _jaccard(cause_tokens, _tokens(components["cause"]))
            solution_score = _jaccard(solution_tokens, _tokens(components["solution"]))
            class_score, class_reasons = self._classification_score(query, record)
            org_score, org_reasons = self._organization_score(query, record)

            component_scores = {
                "problem": problem_score,
                "cause": cause_score,
                "classification": class_score,
                "solution": solution_score,
                "organization": org_score,
            }
            available = {
                "problem": bool(problem_tokens),
                "cause": bool(cause_tokens),
                "classification": bool(_text(query.cause_level1) or _text(query.cause_level2)),
                "solution": bool(solution_tokens),
                "organization": bool(
                    _text(query.ipmt)
                    or _text(query.spdt)
                    or _text(query.responsible_department_level2)
                ),
            }
            total_score, applied_weights = self._weighted_score(component_scores, available)

            matched_problem = sorted(problem_tokens & _tokens(components["problem"]))[:20]
            matched_cause = sorted(cause_tokens & _tokens(components["cause"]))[:20]
            matched_solution = sorted(solution_tokens & _tokens(components["solution"]))[:20]

            reasons = []
            if problem_score >= 0.45:
                reasons.append("问题现象高度相似")
            elif problem_score >= 0.25:
                reasons.append("问题现象存在相似性")
            if matched_problem:
                reasons.append("问题共同词：" + "、".join(matched_problem[:8]))
            if query.cause_description:
                if cause_score >= 0.35:
                    reasons.append("原因描述相似")
                if matched_cause:
                    reasons.append("原因共同词：" + "、".join(matched_cause[:8]))
            if query.solution:
                if solution_score >= 0.35:
                    reasons.append("解决措施相似")
                if matched_solution:
                    reasons.append("措施共同词：" + "、".join(matched_solution[:8]))
            reasons.extend(class_reasons)
            reasons.extend(org_reasons)

            scored.append({
                "rank": 0,
                "case_id": record["case_id"],
                "document_id": record["document_id"],
                "title": record["title"],
                "organization": record.get("organization", {}),
                "classification": record.get("classification", {}),
                "filters": record.get("filters", {}),
                "score": round(total_score, 6),
                "score_breakdown": {
                    "problem": round(problem_score, 6),
                    "problem_vector": round(problem_vector, 6),
                    "problem_keyword": round(problem_keyword, 6),
                    "cause": round(cause_score, 6),
                    "classification": round(class_score, 6),
                    "solution": round(solution_score, 6),
                    "organization": round(org_score, 6),
                },
                "applied_weights": {
                    key: round(value, 6)
                    for key, value in applied_weights.items()
                },
                "matched_problem_keywords": matched_problem,
                "matched_cause_keywords": matched_cause,
                "matched_solution_keywords": matched_solution,
                "reasons": reasons or ["综合召回命中"],
                "quality_flags": record.get("quality_flags", []),
                "retrieval_doc_path": record["retrieval_doc_path"],
            })

        scored.sort(key=lambda item: item["score"], reverse=True)
        minimum = float(self.config.get("minimum_score", 0.0))
        limit = int(top_k or self.config.get("top_k", 10))
        min_returned = max(0, min(limit, int(self.config.get("minimum_returned_candidates", 0))))
        threshold_results = [item for item in scored if item["score"] >= minimum]
        # Recall safeguard: a high threshold must not result in zero candidates.
        if len(threshold_results) < min_returned:
            selected_ids = {item["case_id"] for item in threshold_results}
            for item in scored:
                if item["case_id"] not in selected_ids:
                    threshold_results.append(item)
                    selected_ids.add(item["case_id"])
                if len(threshold_results) >= min_returned:
                    break
        results = threshold_results[:limit]
        for index, item in enumerate(results, start=1):
            item["rank"] = index

        return {
            "query": query.to_dict(),
            "embedding_model": query_embedding.model,
            "scoring_mode": "DYNAMIC_COMPOSITE",
            "total_index_records": len(records),
            "total_candidates": len(candidates),
            "returned_count": len(results),
            "results": results,
        }
