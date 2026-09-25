"""Artifact-backed retrieval adapter for formally published Major cases."""
from __future__ import annotations

import re
from typing import Any

from repositories import JsonArtifactRepository
from retriever.case_retriever import QueryInput


def _tokens(value: str) -> set[str]:
    text = str(value or "").lower()
    words = set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{2,}", text))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    words.update(chinese[index:index + 2] for index in range(max(0, len(chinese) - 1)))
    return {word for word in words if word}


class MajorPublishedCaseSearchAdapter:
    """Search the actual published retrieval documents, never Major SQLite rows."""

    def __init__(self, repository: JsonArtifactRepository) -> None:
        self.repository = repository

    def search(self, query: QueryInput, top_k: int | None = None) -> dict[str, Any]:
        query_tokens = _tokens(query.text)
        results: list[dict[str, Any]] = []
        for path in self.repository.list("knowledge/retrieval_docs"):
            document = self.repository.load(path)
            if not isinstance(document, dict):
                continue
            case_id = str(document.get("case_id") or "").strip()
            text = str(document.get("text") or "")
            if not case_id or not text:
                continue
            common = query_tokens & _tokens(text)
            if query_tokens and not common:
                continue
            score = len(common) / max(1, len(query_tokens)) if query_tokens else 0.0
            results.append({
                "case_id": case_id,
                "title": document.get("title") or case_id,
                "summary": document.get("title") or case_id,
                "score": score,
                "rank": 0,
                "reasons": ["命中正式 Historical Case 检索文档"],
                "matched_fields": ["retrieval_document"],
            })
        results.sort(key=lambda item: (-float(item["score"]), str(item["case_id"])))
        for index, result in enumerate(results, start=1):
            result["rank"] = index
        return {"results": results[:top_k] if top_k else results}
