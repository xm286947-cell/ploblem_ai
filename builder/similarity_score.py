from __future__ import annotations

from typing import Any, Mapping

# 组织上下文只用于候选筛选/适用性判断，不参与相似度评分。
CONTEXT_DIMENSIONS = {"organization_context", "organisation_context", "org_context"}


def _analysis_payload(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    analysis = value.get("analysis")
    return analysis if isinstance(analysis, Mapping) else value


def dimension_scores(similarity: Mapping[str, Any] | None) -> dict[str, float | None]:
    analysis = _analysis_payload(similarity)
    raw_dimensions = analysis.get("dimensions") or {}
    result: dict[str, float | None] = {}
    if not isinstance(raw_dimensions, Mapping):
        return result
    for key, value in raw_dimensions.items():
        name = str(key)
        if name in CONTEXT_DIMENSIONS:
            continue
        score: float | None = None
        if isinstance(value, Mapping) and value.get("score") is not None:
            try:
                score = max(0.0, min(100.0, float(value.get("score"))))
            except (TypeError, ValueError):
                score = None
        result[name] = score
    return result


def calculate_similarity_score(similarity: Mapping[str, Any] | None) -> float | None:
    """根据有效业务维度计算综合相似度；组织上下文永不参与。"""
    scores = [score for score in dimension_scores(similarity).values() if score is not None]
    if scores:
        return round(sum(scores) / len(scores), 2)
    analysis = _analysis_payload(similarity)
    # 兼容旧数据：只有 overall_score 且没有维度时才使用旧综合分。
    raw = analysis.get("overall_score")
    try:
        return max(0.0, min(100.0, float(raw))) if raw is not None else None
    except (TypeError, ValueError):
        return None
