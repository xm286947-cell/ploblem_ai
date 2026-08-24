from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class SchemaRegistry:
    def __init__(self) -> None:
        self._models: dict[str, type[BaseModel]] = {}

    def register(self, name: str, model: type[T]) -> None:
        if not name:
            raise ValueError("schema name must not be empty")
        self._models[name] = model

    def get(self, name: str) -> type[BaseModel]:
        try:
            return self._models[name]
        except KeyError as exc:
            raise KeyError(f"schema not registered: {name}") from exc

    def runtime_schema(self, name: str) -> dict:
        return self.get(name).model_json_schema(mode="validation")

    def names(self) -> list[str]:
        return sorted(self._models)
