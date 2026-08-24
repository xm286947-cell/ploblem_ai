from __future__ import annotations
from pathlib import Path
from typing import Any

from builder.ai_client import OpenAICompatibleClient
from quality_knowledge.model_config import load_quality_issue_ai_config, validate_quality_issue_ai_config


def check_ai(root: str | Path, *, live: bool = False) -> dict[str, Any]:
    result = validate_quality_issue_ai_config(root, require_enabled=True)
    result['live_requested'] = bool(live)
    result['live_status'] = 'NOT_RUN'
    if not result['ok'] or not live:
        return result
    cfg, _ = load_quality_issue_ai_config(root)
    try:
        r = OpenAICompatibleClient(cfg).complete([
            {'role':'system','content':'Return strict JSON only.'},
            {'role':'user','content':'Return exactly {"ok":true}'}
        ])
        result['live_status'] = 'OK'
        result['response_model'] = r.model
        result['response_preview'] = r.content[:300]
    except Exception as exc:
        result['live_status'] = 'FAILED'
        result['live_error'] = str(exc)
        result['ok'] = False
    return result
