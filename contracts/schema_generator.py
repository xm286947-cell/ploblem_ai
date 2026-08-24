from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def generate_runtime_schema(model: type[T]) -> dict:
    return model.model_json_schema(mode="validation")


def write_runtime_schema(model: type[T], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(generate_runtime_schema(model), ensure_ascii=False, indent=2), encoding="utf-8")
    return path
