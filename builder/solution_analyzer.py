from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json

import yaml

from builder.ai_client import AIClientError, MockAIClient, OpenAICompatibleClient
from builder.validators import validate_json
from builder.solution_context import build_solution_payload, payload_chars


SOLUTION_ANALYZER_VERSION = "M8.3-S1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_json(text: str) -> dict[str, Any]:
    content = text.strip()
    if content.startswith("```"):
        lines = content.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        raise ValueError("AI输出中未找到JSON对象")
    value = json.loads(content[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("AI输出不是JSON对象")
    return value


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _empty_analysis() -> dict[str, Any]:
    return {
        "historical_solution_summary": "",
        "corrective_actions": [],
        "preventive_actions": [],
        "verification_evidence": [],
        "closure_status": "UNKNOWN",
        "effectiveness": "UNKNOWN",
        "applicability": "UNKNOWN",
        "reusable_actions": [],
        "adaptation_required": [],
        "reuse_risks": [],
        "evidence_gaps": ["AI解决方案分析未执行"],
        "analysis_summary": "",
        "confidence": 0.0,
    }


class SolutionAnalyzer:
    """分析历史候选案例的解决措施、有效性证据和对当前问题的复用价值。"""

    def __init__(self, root: str | Path, mock: bool = False, client: Any | None = None) -> None:
        self.root = Path(root)
        cfg = yaml.safe_load((self.root / "config/model.yaml").read_text(encoding="utf-8")) or {}
        # Solution AI inherits the common provider/model settings and only overrides
        # fields explicitly configured for M8.3. This keeps existing deployments
        # compatible while allowing independent timeout/token tuning.
        self.ai_cfg = dict(cfg.get("ai") or {})
        self.ai_cfg.update(cfg.get("solution_ai") or {})
        self.prompt_path = self.root / "prompts/solution_analyzer.md"
        self.prompt_template = self.prompt_path.read_text(encoding="utf-8")
        self.prompt_version = str(self.ai_cfg.get("prompt_version") or _hash(self.prompt_path))
        self.schema_path = self.root / "schema/solution_analysis.schema.json"
        self.mock = mock
        self.client_provided = client is not None
        if client is not None:
            self.client = client
        elif mock:
            self.client = MockAIClient(self.root / "tests/samples/mock_solution_response.json")
        else:
            self.client = OpenAICompatibleClient(self.ai_cfg)

    def _messages(self, context: dict[str, Any], similarity: dict[str, Any] | None) -> list[dict[str, str]]:
        payload = build_solution_payload(context, similarity)
        return [
            {"role": "system", "content": self.prompt_template},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
        ]

    def input_diagnostics(self, context: dict[str, Any], similarity: dict[str, Any] | None) -> dict[str, int]:
        compact = build_solution_payload(context, similarity)
        full = {
            "query": context.get("query", {}),
            "candidate": context.get("candidate", {}),
            "historical_case": context.get("case", {}),
            "evidence": context.get("evidence", {}),
            "quality": context.get("quality", {}),
            "similarity_analysis": similarity or {},
        }
        return {
            "compact_prompt_chars": payload_chars(compact),
            "previous_payload_chars": payload_chars(full),
        }

    @staticmethod
    def _similarity_ref(similarity: dict[str, Any] | None) -> dict[str, Any]:
        analysis = (similarity or {}).get("analysis") or {}
        return {
            "overall_score": int(analysis.get("overall_score") or 0),
            "overall_level": str(analysis.get("overall_level") or "UNKNOWN"),
            "analysis_status": str((similarity or {}).get("analysis_status") or "MISSING"),
        }

    def _build(self, context: dict[str, Any], similarity: dict[str, Any] | None,
               analysis: dict[str, Any], status: str, model_name: str,
               warnings: list[dict[str, str]]) -> dict[str, Any]:
        qid, cid = str(context.get("query_id") or ""), str(context.get("case_id") or "")
        return {
            "metadata": {
                "query_id": qid,
                "case_id": cid,
                "source_analysis_context": f"knowledge/analysis_context/{qid}/{cid}.json",
                "source_similarity_analysis": f"knowledge/similarity_analysis/{qid}/{cid}.json" if similarity else "",
                "analyzer_version": SOLUTION_ANALYZER_VERSION,
                "prompt_version": self.prompt_version,
                "model_provider": str(self.ai_cfg.get("provider", "openai_compatible")),
                "model_name": model_name,
                "generated_at": _now(),
            },
            "similarity_reference": self._similarity_ref(similarity),
            "analysis": analysis,
            "analysis_status": status,
            "warnings": warnings,
        }

    def analyze(self, context: dict[str, Any], similarity: dict[str, Any] | None = None,
                skip_ai: bool = False) -> dict[str, Any]:
        configured_model = str(self.ai_cfg.get("model") or "")
        initial_warnings: list[dict[str, str]] = []
        if similarity is None:
            initial_warnings.append({"code": "SIMILARITY_ANALYSIS_MISSING", "message": "未找到M8.2结果，复用判断置信度可能下降"})
        if skip_ai or (not bool(self.ai_cfg.get("enabled", False)) and not self.mock and not self.client_provided):
            return self._build(
                context, similarity, _empty_analysis(), "SKIPPED", configured_model,
                initial_warnings + [{"code": "SOLUTION_AI_SKIPPED", "message": "AI未启用或显式跳过"}],
            )
        try:
            response = self.client.complete(self._messages(context, similarity))
            analysis = _extract_json(response.content)
            result = self._build(context, similarity, analysis, "SUCCESS", response.model, initial_warnings)
            errors = validate_json(result, self.schema_path)
            if errors:
                return self._build(
                    context, similarity, _empty_analysis(), "AI_OUTPUT_INVALID", response.model,
                    initial_warnings + [{"code": "SOLUTION_SCHEMA_INVALID", "message": "; ".join(errors)}],
                )
            return result
        except (AIClientError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
            return self._build(
                context, similarity, _empty_analysis(), "AI_ANALYSIS_FAILED", configured_model,
                initial_warnings + [{"code": "SOLUTION_ANALYSIS_FAILED", "message": str(exc)}],
            )
