from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json

import yaml

from builder.ai_client import AIClientError, MockAIClient, OpenAICompatibleClient
from builder.validators import validate_json
from builder.json_response import parse_json_object, save_raw_response


SIMILARITY_ANALYZER_VERSION = "M8.2-S1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_json(text: str) -> tuple[dict[str, Any], bool]:
    return parse_json_object(text, allow_repair=True)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _empty_analysis() -> dict[str, Any]:
    dimensions = [
        "problem_object", "phenomenon", "trigger_condition", "impact",
        "failure_mechanism", "trc", "mrc", "root_cause",
        "classification", "organization_context",
    ]
    return {
        "dimensions": {
            key: {
                "score": 0,
                "assessment": "UNKNOWN",
                "query_evidence": [],
                "case_evidence": [],
                "reason": "未执行AI相似性分析",
            }
            for key in dimensions
        },
        "overall_score": 0,
        "overall_level": "UNKNOWN",
        "key_similarities": [],
        "key_differences": [],
        "evidence_gaps": ["AI相似性分析未执行"],
        "analysis_summary": "",
        "confidence": 0.0,
    }


class SimilarityAnalyzer:
    """基于Analysis Context逐维比较新问题与历史候选案例。"""

    def __init__(self, root: str | Path, mock: bool = False, client: Any | None = None) -> None:
        self.root = Path(root)
        cfg = yaml.safe_load((self.root / "config/model.yaml").read_text(encoding="utf-8")) or {}
        self.ai_cfg = cfg.get("similarity_ai") or cfg.get("ai") or {}
        self.prompt_path = self.root / "prompts/similarity_analyzer.md"
        self.prompt_template = self.prompt_path.read_text(encoding="utf-8")
        self.prompt_version = str(self.ai_cfg.get("prompt_version") or _hash(self.prompt_path))
        self.schema_path = self.root / "schema/similarity_analysis.schema.json"
        self.mock = mock
        self.client_provided = client is not None
        if client is not None:
            self.client = client
        elif mock:
            self.client = MockAIClient(self.root / "tests/samples/mock_similarity_response.json")
        else:
            self.client = OpenAICompatibleClient(self.ai_cfg)

    def _messages(self, context: dict[str, Any]) -> list[dict[str, str]]:
        payload = {
            "query_id": context.get("query_id"),
            "case_id": context.get("case_id"),
            "query": context.get("query", {}),
            "candidate": context.get("candidate", {}),
            "case": context.get("case", {}),
            "evidence": context.get("evidence", {}),
            "quality": context.get("quality", {}),
        }
        return [
            {"role": "system", "content": self.prompt_template},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
        ]

    def _build(self, context: dict[str, Any], analysis: dict[str, Any], status: str,
               model_name: str, warnings: list[dict[str, str]]) -> dict[str, Any]:
        return {
            "metadata": {
                "query_id": str(context.get("query_id") or ""),
                "case_id": str(context.get("case_id") or ""),
                "source_analysis_context": str((context.get("source_paths") or {}).get("candidate_file") or ""),
                "analyzer_version": SIMILARITY_ANALYZER_VERSION,
                "prompt_version": self.prompt_version,
                "model_provider": str(self.ai_cfg.get("provider", "openai_compatible")),
                "model_name": model_name,
                "generated_at": _now(),
            },
            "retrieval": {
                "rank": int((context.get("candidate") or {}).get("rank") or 0),
                "retrieval_score": float((context.get("candidate") or {}).get("score") or 0.0),
            },
            "analysis": analysis,
            "analysis_status": status,
            "warnings": warnings,
        }

    def analyze(self, context: dict[str, Any], skip_ai: bool = False) -> dict[str, Any]:
        configured_model = str(self.ai_cfg.get("model") or "")
        if skip_ai or (not bool(self.ai_cfg.get("enabled", False)) and not self.mock and not self.client_provided):
            return self._build(
                context, _empty_analysis(), "SKIPPED", configured_model,
                [{"code": "SIMILARITY_AI_SKIPPED", "message": "AI未启用或显式跳过"}],
            )
        query_id = str(context.get("query_id") or "")
        case_id = str(context.get("case_id") or "")
        last_error: Exception | None = None
        messages = self._messages(context)
        for attempt in (1, 2):
            try:
                response = self.client.complete(messages)
                save_raw_response(self.root, "similarity", query_id, case_id, response.content, attempt)
                analysis, repaired = _extract_json(response.content)
                warnings = ([{"code": "AI_JSON_REPAIRED", "message": "AI输出存在常见JSON格式错误，已自动修复"}] if repaired else [])
                result = self._build(context, analysis, "SUCCESS", response.model, warnings)
                errors = validate_json(result, self.schema_path)
                if errors:
                    raise ValueError("SIMILARITY_SCHEMA_INVALID: " + "; ".join(errors))
                return result
            except (AIClientError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
                last_error = exc
                if attempt == 1:
                    messages = messages + [{
                        "role": "user",
                        "content": "上一次输出无法解析。请仅重新输出严格合法的JSON对象，不要Markdown，不要解释，检查逗号、引号和转义字符。",
                    }]
                    continue
        schema_invalid = bool(last_error and str(last_error).startswith("SIMILARITY_SCHEMA_INVALID:"))
        return self._build(
            context, _empty_analysis(), "AI_OUTPUT_INVALID" if schema_invalid else "AI_ANALYSIS_FAILED", configured_model,
            [{
                "code": "SIMILARITY_SCHEMA_INVALID" if schema_invalid else "SIMILARITY_ANALYSIS_FAILED",
                "message": str(last_error),
            }],
        )
