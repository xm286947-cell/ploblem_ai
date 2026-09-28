"""Validation helpers for cross-page Overall return state.

Only navigation state is carried here. Evidence and Source payloads stay with
their producing domains and are accepted through common-evidence/v1.0.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import HTTPException


OVERALL_RETURN_CONTEXT_VERSION = "overall-return-context/v1"
_FIELD_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def normalize_overall_return_state(raw: str | None) -> dict[str, Any] | None:
    """Validate the one-shot state used to restore a page after detail/source."""

    if raw is None or raw == "":
        return None
    if len(raw.encode("utf-8")) > 4096:
        raise HTTPException(status_code=400, detail="OVERALL_RETURN_CONTEXT_INVALID")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="OVERALL_RETURN_CONTEXT_INVALID")
    allowed = {"contract", "scroll_y", "focus_id", "fields", "selected_object", "tab"}
    if (
        not isinstance(value, dict)
        or set(value) - allowed
        or value.get("contract") != OVERALL_RETURN_CONTEXT_VERSION
    ):
        raise HTTPException(status_code=400, detail="OVERALL_RETURN_CONTEXT_INVALID")
    scroll_y = value.get("scroll_y", 0)
    if isinstance(scroll_y, bool) or not isinstance(scroll_y, int) or not 0 <= scroll_y <= 10_000_000:
        raise HTTPException(status_code=400, detail="OVERALL_RETURN_CONTEXT_INVALID")
    result: dict[str, Any] = {"contract": OVERALL_RETURN_CONTEXT_VERSION, "scroll_y": scroll_y}
    for key in ("focus_id", "selected_object", "tab"):
        item = value.get(key)
        if item is not None:
            if not isinstance(item, str) or len(item) > 300:
                raise HTTPException(status_code=400, detail="OVERALL_RETURN_CONTEXT_INVALID")
            result[key] = item
    fields = value.get("fields", {})
    if (
        not isinstance(fields, dict)
        or len(fields) > 32
        or any(
            not isinstance(key, str)
            or not _FIELD_KEY.fullmatch(key)
            or not isinstance(item, str)
            or len(item) > 300
            for key, item in fields.items()
        )
    ):
        raise HTTPException(status_code=400, detail="OVERALL_RETURN_CONTEXT_INVALID")
    result["fields"] = dict(fields)
    return result


def encode_overall_return_state(state: dict[str, Any] | None) -> str:
    if state is None:
        return ""
    return json.dumps(state, ensure_ascii=False, separators=(",", ":"))


def append_overall_return_state(target: str, state: dict[str, Any] | None) -> str:
    """Attach one validated restoration payload while preserving query/hash."""

    if state is None:
        return target
    parts = urlsplit(target)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != "overall_return_state"]
    query.append(("overall_return_state", encode_overall_return_state(state)))
    return urlunsplit(("", "", parts.path, urlencode(query), parts.fragment))
