"""Repository package public exports with lazy loading."""

from __future__ import annotations

from typing import Any


__all__ = ["JsonArtifactRepository", "RepositoryError"]


def __getattr__(name: str) -> Any:
    if name in {"JsonArtifactRepository", "RepositoryError"}:
        from .json_repository import JsonArtifactRepository, RepositoryError
        values = {
            "JsonArtifactRepository": JsonArtifactRepository,
            "RepositoryError": RepositoryError,
        }
        value = values[name]
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
