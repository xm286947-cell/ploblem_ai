from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

import yaml


class ConfigError(RuntimeError):
    """Raised when a configuration file cannot be loaded safely."""


class ConfigLoader:
    """Central YAML configuration loader with path validation and caching."""

    _cache: dict[Path, dict[str, Any]] = {}
    _lock = RLock()

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.config_dir = self.project_root / "config"

    def load(self, name: str, *, required: bool = True, use_cache: bool = True) -> dict[str, Any]:
        path = self._resolve_config_path(name)
        if not path.exists():
            if required:
                raise ConfigError(f"配置文件不存在: {path}")
            return {}

        with self._lock:
            if use_cache and path in self._cache:
                return deepcopy(self._cache[path])

            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, yaml.YAMLError) as exc:
                raise ConfigError(f"配置文件读取失败: {path}: {exc}") from exc

            if raw is None:
                value: dict[str, Any] = {}
            elif isinstance(raw, Mapping):
                value = dict(raw)
            else:
                raise ConfigError(f"配置文件根节点必须是对象: {path}")

            self._cache[path] = value
            return deepcopy(value)

    def load_all(self, *, required_names: tuple[str, ...] = ()) -> dict[str, dict[str, Any]]:
        if not self.config_dir.exists():
            raise ConfigError(f"配置目录不存在: {self.config_dir}")
        result: dict[str, dict[str, Any]] = {}
        for path in sorted(self.config_dir.glob("*.yaml")):
            result[path.stem] = self.load(path.name)
        for name in required_names:
            if Path(name).stem not in result:
                self.load(name, required=True)
        return result

    def get(self, name: str, dotted_key: str, default: Any = None) -> Any:
        current: Any = self.load(name)
        for part in dotted_key.split("."):
            if not isinstance(current, Mapping) or part not in current:
                return default
            current = current[part]
        return deepcopy(current)

    def path(self, name: str, dotted_key: str, default: str | Path | None = None) -> Path:
        value = self.get(name, dotted_key, default)
        if value in (None, ""):
            raise ConfigError(f"配置项为空: {name}:{dotted_key}")
        candidate = Path(str(value)).expanduser()
        return candidate.resolve() if candidate.is_absolute() else (self.project_root / candidate).resolve()

    @classmethod
    def clear_cache(cls) -> None:
        with cls._lock:
            cls._cache.clear()

    def _resolve_config_path(self, name: str) -> Path:
        text = str(name).strip()
        if not text:
            raise ConfigError("配置文件名不能为空")
        candidate = Path(text)
        if candidate.suffix.lower() not in {".yaml", ".yml"}:
            candidate = candidate.with_suffix(".yaml")
        if candidate.name != str(candidate):
            raise ConfigError(f"配置文件名不允许包含路径: {name}")
        return self.config_dir / candidate.name
