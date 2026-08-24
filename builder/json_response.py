from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re


class JSONResponseError(ValueError):
    pass


def save_raw_response(root: Path, stage: str, query_id: str, case_id: str, content: str, attempt: int = 1) -> Path:
    safe_query = query_id or "UNKNOWN_QUERY"
    safe_case = case_id or "UNKNOWN_CASE"
    path = root / "output" / "raw_ai" / stage / safe_query
    path.mkdir(parents=True, exist_ok=True)
    target = path / f"{safe_case}.attempt{attempt}.txt"
    target.write_text(content or "", encoding="utf-8")
    return target


def _strip_fence(text: str) -> str:
    content = (text or "").strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    return content


def _slice_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise JSONResponseError("AI响应中未找到JSON对象")
    return text[start:end + 1]


def _repair_common_json_errors(content: str) -> str:
    repaired = content
    repaired = repaired.replace("，", ",").replace("：", ":")
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    # Insert a comma between a completed JSON value and the next quoted key.
    repaired = re.sub(
        r'("(?:[^"\\]|\\.)*"|true|false|null|-?\d+(?:\.\d+)?)\s*\n(\s*"[^"\n]+"\s*:)',
        r'\1,\n\2',
        repaired,
    )
    # Repair adjacent object/array endings followed by a new key.
    repaired = re.sub(r'([}\]])\s*\n(\s*"[^"\n]+"\s*:)', r'\1,\n\2', repaired)
    return repaired


def parse_json_object(text: str, allow_repair: bool = True) -> tuple[dict[str, Any], bool]:
    raw = _slice_object(_strip_fence(text))
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise JSONResponseError("AI响应不是JSON对象")
        return value, False
    except json.JSONDecodeError as first_error:
        if not allow_repair:
            raise JSONResponseError(f"AI响应JSON解析失败: {first_error}") from first_error
        repaired = _repair_common_json_errors(raw)
        try:
            value = json.loads(repaired)
        except json.JSONDecodeError as second_error:
            raise JSONResponseError(
                f"AI响应JSON解析失败: {first_error}; 自动修复后仍失败: {second_error}"
            ) from second_error
        if not isinstance(value, dict):
            raise JSONResponseError("AI响应不是JSON对象")
        return value, True
