"""Deterministic P0 seeds for native V2 prompts and insight scoring.

The source prompt files are release artifacts. Their content is hashed together
with the stage contract and model parameters before a clean P0 database can be
marked READY.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from quality_knowledge.models.analysis_v2 import (
    CapabilityGapV2DTO,
    EscapeAnalysisV2DTO,
    OccurrenceAnalysisV2DTO,
    RecurrenceAnalysisV2DTO,
)
from quality_knowledge.taxonomy.seed import TAXONOMY_VERSION_ID


PROMPT_DIRECTORY = Path(__file__).resolve().parents[1] / "prompts_v2"
PROMPT_STAGE_FILES = {
    "occurrence": "occurrence_v2.md",
    "escape": "escape_v2.md",
    "recurrence": "recurrence_v2.md",
    "capability_gap": "capability_gap_v2.md",
}
PROMPT_VERSION_IDS = {
    stage: f"PROMPT-P0-V2-{stage.upper()}-V1" for stage in PROMPT_STAGE_FILES
}
DEFAULT_MODEL_PARAMS = {"temperature": 0, "max_tokens": 4096, "response_format": "json_object"}

# Seven independently auditable scoring components. Their aggregation is a G2-C
# concern; P0 stores this frozen source-of-truth configuration now.
INSIGHT_SCORING_WEIGHTS = {
    "severity": 0.20,
    "recurrence": 0.15,
    "customer_impact": 0.15,
    "control_effectiveness": 0.15,
    "capability_gap": 0.10,
    "evidence_confidence": 0.10,
    "classification_consistency": 0.15,
}
INSIGHT_SCORING_VERSION_ID = "INSIGHT-SCORING-P0-V1"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256(value: Any) -> str:
    encoded = value.encode("utf-8") if isinstance(value, str) else canonical_json(value).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _contract_for_stage(stage: str) -> dict[str, Any]:
    model_by_stage = {
        "occurrence": OccurrenceAnalysisV2DTO,
        "escape": EscapeAnalysisV2DTO,
        "recurrence": RecurrenceAnalysisV2DTO,
    }
    if stage == "capability_gap":
        return {
            "contract_version": "2.0.0",
            "strict_root_keys": ["capability_gaps"],
            "item_required_keys": list(CapabilityGapV2DTO.model_fields),
        }
    return {
        "contract_version": "2.0.0",
        "strict_root_keys": list(model_by_stage[stage].model_fields),
    }


def build_prompt_seeds(prompt_directory: Path | None = None) -> list[dict[str, Any]]:
    directory = prompt_directory or PROMPT_DIRECTORY
    seeds: list[dict[str, Any]] = []
    for stage, filename in PROMPT_STAGE_FILES.items():
        path = directory / filename
        if not path.exists():
            raise ValueError(f"P0_PROMPT_SOURCE_MISSING:{stage}")
        prompt_text = path.read_text(encoding="utf-8")
        if not prompt_text.strip():
            raise ValueError(f"P0_PROMPT_SOURCE_EMPTY:{stage}")
        seed = {
            "prompt_version_id": PROMPT_VERSION_IDS[stage],
            "stage": stage,
            "version_no": 1,
            "status": "ACTIVE",
            "prompt_text": prompt_text,
            "output_contract": _contract_for_stage(stage),
            "taxonomy_version_id": TAXONOMY_VERSION_ID,
            "model_params": DEFAULT_MODEL_PARAMS,
        }
        seed["content_hash"] = sha256(seed)
        seeds.append(seed)
    return seeds


def prompt_set_hash(prompt_directory: Path | None = None) -> str:
    return sha256(build_prompt_seeds(prompt_directory))


def build_scoring_seed() -> dict[str, Any]:
    seed = {
        "scoring_version_id": INSIGHT_SCORING_VERSION_ID,
        "version_no": 1,
        "status": "ACTIVE",
        "weights": INSIGHT_SCORING_WEIGHTS,
    }
    if round(sum(seed["weights"].values()), 8) != 1.0:
        raise ValueError("P0_SCORING_WEIGHTS_INVALID")
    seed["content_hash"] = sha256(seed)
    return seed


def scoring_seed_hash() -> str:
    return sha256(build_scoring_seed())
