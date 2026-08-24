from __future__ import annotations

import re


def conservative_json_repair(text: str) -> tuple[str, bool]:
    """Only apply low-risk repairs. Semantic field repair belongs in adapters."""
    repaired = text.strip().lstrip("\ufeff")
    original = repaired
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    return repaired, repaired != original
