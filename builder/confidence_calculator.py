from __future__ import annotations

from typing import Any, Mapping

from builder.similarity_score import dimension_scores


def _analysis_payload(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    analysis = value.get("analysis")
    return analysis if isinstance(analysis, Mapping) else value


def calculate_confidence(
    similarity: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None = None,
    decision_confidence: Any = None,
) -> dict[str, Any]:
    """计算判断置信度，不把相似度本身当作置信度。

    置信度由证据覆盖、AI自报置信度和案例数据完整度组成。
    """
    analysis = _analysis_payload(similarity)
    dimensions = analysis.get("dimensions") or {}
    scores = dimension_scores(similarity)
    total = len(scores)
    scored = sum(1 for score in scores.values() if score is not None)
    score_coverage = (scored / total) if total else 0.0

    evidence_dimensions = 0
    inferred_dimensions = 0
    if isinstance(dimensions, Mapping):
        for key, dim in dimensions.items():
            if str(key) not in scores or not isinstance(dim, Mapping):
                continue
            query_evidence = dim.get("query_evidence") or []
            case_evidence = dim.get("case_evidence") or []
            if query_evidence and case_evidence:
                evidence_dimensions += 1
            assessment = str(dim.get("assessment") or "").upper()
            reason = str(dim.get("reason") or "")
            if "INFER" in assessment or "推断" in reason or "猜测" in reason:
                inferred_dimensions += 1
    evidence_coverage = (evidence_dimensions / total) if total else 0.0

    explicit = decision_confidence
    if explicit in (None, "", 0, 0.0):
        explicit = analysis.get("confidence")
    try:
        ai_confidence = float(explicit)
        if ai_confidence > 1:
            ai_confidence /= 100.0
        ai_confidence = max(0.0, min(1.0, ai_confidence))
    except (TypeError, ValueError):
        ai_confidence = 0.0

    quality_status = str(((context or {}).get("quality") or {}).get("status") or "").upper()
    quality_factor = {"COMPLETE": 1.0, "PARTIAL": 0.7, "INCOMPLETE": 0.4}.get(quality_status, 0.6)
    inference_penalty = min(0.25, inferred_dimensions * 0.05)

    # 无AI置信度时，仅由证据质量决定；避免回退为相似度。
    if ai_confidence > 0:
        confidence = 0.45 * evidence_coverage + 0.25 * score_coverage + 0.20 * ai_confidence + 0.10 * quality_factor
    else:
        confidence = 0.55 * evidence_coverage + 0.30 * score_coverage + 0.15 * quality_factor
    confidence = max(0.0, min(1.0, confidence - inference_penalty))

    reasons: list[str] = []
    reasons.append(f"已评分维度 {scored}/{total}" if total else "无可用相似维度")
    reasons.append(f"双侧证据完整维度 {evidence_dimensions}/{total}" if total else "无双侧证据")
    if ai_confidence > 0:
        reasons.append(f"AI判断置信度 {round(ai_confidence * 100)}%")
    else:
        reasons.append("AI未提供独立置信度")
    if inferred_dimensions:
        reasons.append(f"存在 {inferred_dimensions} 个推断维度")
    if quality_status:
        reasons.append(f"案例证据状态 {quality_status}")

    return {
        "score": round(confidence, 4),
        "evidence_coverage": round(evidence_coverage, 4),
        "score_coverage": round(score_coverage, 4),
        "ai_confidence": round(ai_confidence, 4),
        "inferred_dimensions": inferred_dimensions,
        "reasons": reasons,
    }
