from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
import hashlib
import json

import yaml

from builder.ai_client import AIClientError, MockAIClient, OpenAICompatibleClient
from builder.validators import validate_json
from builder.json_response import parse_json_object, save_raw_response
from builder.evidence_schema_migration import migrate_inferred_evidence, missing_required_fields

AI_ENRICHER_VERSION = "M7.2-A1"


class AIEnricherConfigError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_json(text: str) -> tuple[dict[str, Any], bool]:
    return parse_json_object(text, allow_repair=True)


def _empty_fact(value: Any = "") -> dict[str, Any]:
    return {
        "value": value,
        "evidence_type": "UNKNOWN",
        "confidence": 0.0,
        "reason": "",
    }


def empty_inferred() -> dict[str, Any]:
    return {
        "problem_summary": _empty_fact(""),
        "standard_problem_description": _empty_fact(""),
        "failure_objects": [],
        "phenomena": [],
        "trigger_conditions": [],
        "impacts": [],
        "operating_context": [],
        "trc": _empty_fact(""),
        "mrc": _empty_fact(""),
        "root_causes": [],
        "failure_mechanisms": [],
        "contributing_factors": [],
        "classification": {
            "cause_level1": _empty_fact(""),
            "cause_level2": _empty_fact(""),
            "cause_level3": _empty_fact(""),
            "cause_level4": _empty_fact(""),
        },
        "keywords": [],
        "tags": [],
        "solution": {
            "current_solution": _empty_fact(""),
            "solution_object": _empty_fact(""),
            "solution_mechanism": _empty_fact(""),
            "expected_effect": _empty_fact(""),
        },
        "information_gaps": [],
        "overall_confidence": _empty_fact(0.0),
    }


def _stable_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


class QueryAIEnricher:
    def __init__(
        self,
        root: str | Path,
        mock: bool = False,
        client: Any | None = None,
    ) -> None:
        self.root = Path(root)
        self.model_config_path = self.root / "config/model.yaml"
        raw_cfg = yaml.safe_load(self.model_config_path.read_text(encoding="utf-8")) or {}
        self.ai_cfg = raw_cfg.get("ai") or {}
        if not isinstance(self.ai_cfg, dict):
            raise AIEnricherConfigError("model.yaml中的ai配置无效")
        self.prompt_path = self.root / "prompts/query_ai_enricher.md"
        if not self.prompt_path.exists():
            raise AIEnricherConfigError(f"Prompt不存在: {self.prompt_path}")
        self.prompt_template = self.prompt_path.read_text(encoding="utf-8")
        self.prompt_version = str(self.ai_cfg.get("prompt_version") or _stable_hash(self.prompt_path))
        self.schema_path = self.root / "schema/enriched_query.schema.json"
        self.mock = mock
        self.client_provided = client is not None
        if client is not None:
            self.client = client
        elif mock:
            response_file = self.root / "tests/samples/mock_query_ai_response.json"
            self.client = MockAIClient(response_file=response_file)
        else:
            self.client = OpenAICompatibleClient(self.ai_cfg)

    def _messages(self, normalized_query: dict[str, Any]) -> list[dict[str, str]]:
        payload = {
            "original": normalized_query.get("original", {}),
            "normalized": normalized_query.get("normalized", {}),
            "extensions": normalized_query.get("extensions", {}),
        }
        return [
            {"role": "system", "content": self.prompt_template},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
        ]

    @staticmethod
    def _build_result(
        normalized_query: dict[str, Any],
        inferred: dict[str, Any],
        source_path: str,
        model_provider: str,
        model_name: str,
        prompt_version: str,
        status: str,
        warnings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        metadata = normalized_query.get("metadata") or {}
        return {
            "metadata": {
                "query_id": str(metadata.get("query_id", "")),
                "source_normalized_query": source_path,
                "normalizer_version": str(metadata.get("normalizer_version", "")),
                "normalization_config_version": str(metadata.get("normalization_config_version", "")),
                "ai_enricher_version": AI_ENRICHER_VERSION,
                "prompt_version": prompt_version,
                "model_provider": model_provider,
                "model_name": model_name,
                "generated_at": _now(),
            },
            "original": deepcopy(normalized_query.get("original") or {}),
            "normalized": deepcopy(normalized_query.get("normalized") or {}),
            "extensions": deepcopy(normalized_query.get("extensions") or {}),
            "inferred": inferred,
            "ai_warnings": warnings,
            "enrich_status": status,
        }

    def enrich(self, normalized_query: dict[str, Any], source_path: str = "", skip_ai: bool = False) -> dict[str, Any]:
        original_before = deepcopy(normalized_query.get("original") or {})
        normalized_before = deepcopy(normalized_query.get("normalized") or {})
        provider = str(self.ai_cfg.get("provider", "openai_compatible"))
        configured_model = str(self.ai_cfg.get("model", ""))

        if skip_ai or (not bool(self.ai_cfg.get("enabled", False)) and not self.mock and not self.client_provided):
            result = self._build_result(
                normalized_query,
                empty_inferred(),
                source_path,
                provider,
                configured_model,
                self.prompt_version,
                "SKIPPED",
                [{"code": "AI_ENRICHMENT_SKIPPED", "message": "AI未启用或显式跳过"}],
            )
            return result

        query_id = str((normalized_query.get("metadata") or {}).get("query_id") or "")
        messages = self._messages(normalized_query)
        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                response = self.client.complete(messages)
                save_raw_response(self.root, "query_enrichment", query_id, "QUERY", response.content, attempt)
                inferred, repaired = _extract_json(response.content)
                missing_fields = missing_required_fields(inferred)
                if missing_fields:
                    raise ValueError("AI_OUTPUT_REQUIRED_FIELDS_MISSING: " + ", ".join(missing_fields))
                inferred, migrated = migrate_inferred_evidence(inferred)
                candidate = self._build_result(
                    normalized_query, inferred, source_path, provider, response.model,
                    self.prompt_version, "SUCCESS",
                    (([{"code": "AI_JSON_REPAIRED", "message": "AI输出存在常见JSON格式错误，已自动修复"}] if repaired else [])
                     + ([{"code": "AI_EVIDENCE_SCHEMA_MIGRATED", "message": "AI输出字段形态已迁移为统一Evidence结构"}] if migrated else [])),
                )
                errors = validate_json(candidate, self.schema_path)
                if errors:
                    raise ValueError("AI_OUTPUT_SCHEMA_INVALID: " + "; ".join(errors))
                if candidate["original"] != original_before or candidate["normalized"] != normalized_before:
                    raise RuntimeError("AI_ENRICHER_READONLY_VIOLATION")
                return candidate
            except (AIClientError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
                last_error = exc
                if attempt == 1:
                    messages = messages + [{
                        "role": "user",
                        "content": "上一次输出无法解析。请仅重新输出严格合法的JSON对象，不要Markdown，不要解释，检查逗号、引号和转义字符。",
                    }]
                    continue
        schema_invalid = bool(last_error and str(last_error).startswith(("AI_OUTPUT_SCHEMA_INVALID:", "AI_OUTPUT_REQUIRED_FIELDS_MISSING:")))
        return self._build_result(
            normalized_query, empty_inferred(), source_path, provider, configured_model,
            self.prompt_version, "AI_OUTPUT_INVALID" if schema_invalid else "AI_ENRICH_FAILED",
            [{
                "code": "AI_OUTPUT_SCHEMA_INVALID" if schema_invalid else "AI_ENRICH_FAILED",
                "message": str(last_error),
            }],
        )
