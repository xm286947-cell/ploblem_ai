from __future__ import annotations

import json
from typing import Any


class JSONExtractionError(ValueError):
    pass


def extract_json(raw_output: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw_output, dict):
        return raw_output
    if not isinstance(raw_output, str) or not raw_output.strip():
        raise JSONExtractionError("AI output is empty")

    text = raw_output.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise JSONExtractionError("top-level JSON must be an object")
        return data
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise JSONExtractionError("no complete JSON object found")
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise JSONExtractionError(str(exc)) from exc
        if not isinstance(data, dict):
            raise JSONExtractionError("top-level JSON must be an object")
        return data
