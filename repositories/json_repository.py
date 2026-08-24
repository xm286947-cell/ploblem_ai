from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable


class RepositoryError(RuntimeError):
    """Raised when a repository operation cannot be completed safely."""


class JsonArtifactRepository:
    """Safe JSON artifact access rooted in one project directory.

    The repository centralizes UTF-8 decoding, JSON validation, path-boundary
    checks and atomic writes. Business modules should not duplicate these
    concerns when reading knowledge or runtime artifacts.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def resolve(self, path: str | Path) -> Path:
        candidate = Path(path)
        resolved = candidate.resolve() if candidate.is_absolute() else (self.root / candidate).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise RepositoryError(f"PATH_OUTSIDE_REPOSITORY_ROOT: {path}") from exc
        return resolved

    def exists(self, path: str | Path) -> bool:
        return self.resolve(path).is_file()

    def load(self, path: str | Path, *, required: bool = False) -> dict[str, Any] | None:
        target = self.resolve(path)
        if not target.exists():
            if required:
                raise RepositoryError(f"JSON_ARTIFACT_NOT_FOUND: {target}")
            return None
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            if required:
                raise RepositoryError(f"JSON_ARTIFACT_INVALID: {target}: {exc}") from exc
            return None
        if not isinstance(payload, dict):
            if required:
                raise RepositoryError(f"JSON_ARTIFACT_NOT_OBJECT: {target}")
            return None
        return payload

    def save(self, path: str | Path, payload: dict[str, Any]) -> Path:
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
        return target

    def list(self, directory: str | Path, pattern: str = "*.json") -> list[Path]:
        target = self.resolve(directory)
        if not target.exists():
            return []
        return sorted(path for path in target.glob(pattern) if path.is_file())

    def first_existing(self, paths: Iterable[str | Path]) -> Path | None:
        for path in paths:
            target = self.resolve(path)
            if target.is_file():
                return target
        return None

    def relative(self, path: str | Path | None) -> str:
        if path is None:
            return ""
        target = self.resolve(path)
        return target.relative_to(self.root).as_posix()
