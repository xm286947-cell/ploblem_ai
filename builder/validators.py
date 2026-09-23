from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from jsonschema import Draft202012Validator


class SchemaValidationError(Exception):
    """Raised when JSON data does not comply with the configured schema."""


def load_json(path: str | Path) -> Dict[str, Any]:
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def validate_json(data: Dict[str, Any], schema_path: str | Path) -> List[str]:
    schema = load_json(schema_path)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda item: list(item.absolute_path))

    messages: List[str] = []
    for error in errors:
        location = "$"
        if error.absolute_path:
            location += "." + ".".join(str(item) for item in error.absolute_path)
        messages.append(f"{location}: {error.message}")
    return messages


def validate_json_file(json_path: str | Path, schema_path: str | Path) -> None:
    data = load_json(json_path)
    errors = validate_json(data, schema_path)
    if errors:
        raise SchemaValidationError("\n".join(errors))
