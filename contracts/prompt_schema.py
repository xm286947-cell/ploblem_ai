from __future__ import annotations

from copy import deepcopy
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

_DROP_KEYS = {"title", "default", "examples", "$schema", "$id"}


def _simplify(node: Any) -> Any:
    if isinstance(node, dict):
        result: dict[str, Any] = {}
        for key, value in node.items():
            if key in _DROP_KEYS:
                continue
            result[key] = _simplify(value)
        return result
    if isinstance(node, list):
        return [_simplify(item) for item in node]
    return node


def generate_prompt_schema(model: type[T]) -> dict:
    """Generate a compact model-facing schema from the same DTO definition."""
    return _simplify(deepcopy(model.model_json_schema(mode="validation")))
