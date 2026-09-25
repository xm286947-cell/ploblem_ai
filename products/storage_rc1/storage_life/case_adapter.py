"""Read-only case adapter. Synthetic fixtures are used only without an upstream URL."""
import json
import os
from pathlib import Path
from urllib.parse import quote

import httpx


def _fixtures():
    path = Path(__file__).resolve().parents[1] / "examples" / "synthetic_cases.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _upstream(path: str):
    base = os.environ.get("STORAGE_LIFE_CASE_API_BASE", "").rstrip("/")
    if not base:
        return None
    response = httpx.get(base + path, timeout=5)
    response.raise_for_status()
    return response.json()


def search_cases(keyword: str):
    if os.environ.get("STORAGE_LIFE_CASE_API_BASE"):
        return _upstream("/cases?keyword=" + quote(keyword))
    key = keyword.casefold()
    return [c for c in _fixtures() if key in json.dumps(c, ensure_ascii=False).casefold()]


def get_case(case_id: str):
    if os.environ.get("STORAGE_LIFE_CASE_API_BASE"):
        return _upstream("/cases/" + quote(case_id, safe=""))
    return next((c for c in _fixtures() if c["case_id"] == case_id), None)
