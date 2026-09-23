from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import List
import json

from builder.ai_client import MockAIClient, OpenAICompatibleClient
from builder.ai_response_parser import parse_and_validate


def _ai_value(value: str, confidence: float = 0.75) -> dict:
    return {
        "value": value,
        "source_type": "AI",
        "source_location": "AI_ENRICHER",
        "confidence": confidence,
        "evidence_refs": [],
    }


def _ai_values(values: List[str], confidence: float = 0.75) -> List[dict]:
    return [_ai_value(value, confidence) for value in values if value]


class AIEnricher:
    def __init__(self, project_root: str | Path, model_config: dict, mock: bool = False) -> None:
        self.root = Path(project_root).resolve()
        self.config = model_config["ai"]
        self.prompt_version = str(self.config.get("prompt_version", "1.0"))
        self.system_prompt = (self.root / "prompts/ai_enricher_system.md").read_text(encoding="utf-8")
        self.user_template = (self.root / "prompts/ai_enricher_user.md").read_text(encoding="utf-8")
        self.response_schema = str(self.root / "schema/ai_enricher_response.schema.json")

        if mock:
            mock_path = self.root / str(self.config.get("mock_response_file"))
            self.client = MockAIClient(mock_path)
        else:
            self.client = OpenAICompatibleClient(self.config)

    def enrich(self, standard_case: dict) -> dict:
        facts_snapshot = {
            "metadata": deepcopy(standard_case["metadata"]),
            "business_context": deepcopy(standard_case["business_context"]),
            "problem_original": standard_case["problem"]["original_description"],
            "problem_report": standard_case["problem"]["report_description"],
            "trc_original_report": deepcopy(standard_case["analysis"]["trc"]),
            "mrc_original_report": deepcopy(standard_case["analysis"]["mrc"]),
            "classification_original": deepcopy(standard_case["classification"]["original"]),
        }

        user_prompt = self.user_template.replace(
            "{{STANDARD_CASE_JSON}}",
            json.dumps(standard_case, ensure_ascii=False, indent=2),
        )
        response = self.client.complete([
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_prompt},
        ])
        ai = parse_and_validate(response.content, self.response_schema)

        enriched = deepcopy(standard_case)
        now = datetime.now(timezone.utc).isoformat()

        enriched["problem"]["standard_description"] = ai["standard_description"]
        enriched["problem"]["problem_summary"] = ai["problem_summary"]
        enriched["problem"]["phenomenon"] = _ai_values(ai["phenomenon"])
        enriched["problem"]["failure_object"] = _ai_values(ai["failure_object"])
        enriched["problem"]["trigger_condition"] = _ai_values(ai["trigger_condition"])

        enriched["analysis"]["failure_mechanism"] = _ai_values(ai["failure_mechanism"])
        enriched["analysis"]["contributing_factors"] = _ai_values(ai["contributing_factors"])
        enriched["analysis"]["trc"]["occurrence"]["standard"] = ai["trc_occurrence_standard"]
        enriched["analysis"]["trc"]["escape"]["standard"] = ai["trc_escape_standard"]
        enriched["analysis"]["mrc"]["occurrence"]["standard"] = ai["mrc_occurrence_standard"]
        enriched["analysis"]["mrc"]["escape"]["standard"] = ai["mrc_escape_standard"]

        cls = ai["ai_classification"]
        enriched["classification"]["ai_inferred"] = cls
        original = enriched["classification"]["original"]
        comparable = bool(cls["cause_level1"] or cls["cause_level2"])
        conflict = comparable and (
            (original.get("cause_level1") and cls["cause_level1"] and original["cause_level1"] != cls["cause_level1"])
            or
            (original.get("cause_level2") and cls["cause_level2"] and original["cause_level2"] != cls["cause_level2"])
        )
        enriched["classification"]["classification_conflict"] = bool(conflict)
        enriched["classification"]["conflict_description"] = (
            f"原始分类={original.get('cause_level1','')}/{original.get('cause_level2','')}；"
            f"AI分类={cls['cause_level1']}/{cls['cause_level2']}；理由={cls['reason']}"
            if conflict else ""
        )

        enriched["solution"]["reusable_actions"] = _ai_values(ai["reusable_actions"])
        knowledge = enriched["knowledge"]
        knowledge["case_summary"] = ai["case_summary"]
        knowledge["normalized_problem"] = ai["normalized_problem"]
        for field in [
            "phenomenon_tags","failure_object_tags","trigger_tags",
            "failure_mechanism_tags","cause_tags","solution_tags","keywords",
        ]:
            knowledge[field] = ai[field]
        knowledge["retrieval_text"] = ai["retrieval_text"]
        knowledge["ai_model"] = response.model
        knowledge["prompt_version"] = self.prompt_version
        knowledge["generated_at"] = now
        knowledge["quality_flags"] = [
            flag for flag in knowledge.get("quality_flags", [])
            if flag != "AI_ENRICH_WARNING"
        ]

        enriched["metadata"]["prompt_version"] = self.prompt_version
        enriched["metadata"]["model_version"] = response.model
        enriched["metadata"]["updated_at"] = now
        enriched["metadata"]["generated_at"] = now

        # Hard guard: facts must remain unchanged.
        assert enriched["metadata"]["case_id"] == facts_snapshot["metadata"]["case_id"]
        assert enriched["business_context"] == facts_snapshot["business_context"]
        assert enriched["problem"]["original_description"] == facts_snapshot["problem_original"]
        assert enriched["problem"]["report_description"] == facts_snapshot["problem_report"]
        for group in ("trc", "mrc"):
            old_group = facts_snapshot[f"{group}_original_report"]
            new_group = enriched["analysis"][group]
            for kind in ("occurrence", "escape"):
                assert new_group[kind]["original"] == old_group[kind]["original"]
                assert new_group[kind]["report"] == old_group[kind]["report"]
                assert new_group[kind]["evidence_refs"] == old_group[kind]["evidence_refs"]
        assert enriched["classification"]["original"] == facts_snapshot["classification_original"]

        return enriched
