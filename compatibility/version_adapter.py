from __future__ import annotations

from typing import Any, Callable

Adapter = Callable[[dict[str, Any]], dict[str, Any]]


class VersionAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[tuple[str, str, str], Adapter] = {}

    def register(self, contract_name: str, from_version: str, to_version: str, adapter: Adapter) -> None:
        self._adapters[(contract_name, from_version, to_version)] = adapter

    def adapt(self, contract_name: str, from_version: str, to_version: str, payload: dict[str, Any]) -> dict[str, Any]:
        if from_version == to_version:
            return payload
        key = (contract_name, from_version, to_version)
        if key not in self._adapters:
            raise KeyError(f"compatibility adapter not registered: {key}")
        return self._adapters[key](payload)
