"""Pure-read resolver for the frozen Hardware Knowledge persistent data root.

The resolver never creates directories, files, databases, or schemas. First
install initialization is owned by the later startup coordinator.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Mapping


BOOTSTRAP_CONTRACT_VERSION = "hardware-data-bootstrap/v1"
MANIFEST_CONTRACT_VERSION = "hardware-data-manifest/v1"
DATA_LAYOUT_VERSION = 1
BOOTSTRAP_FILENAME = "bootstrap.json"
MANIFEST_RELATIVE_PATH = Path("manifest/hardware_data_manifest.json")
PERSISTENT_DATA_RELATIVE_PATHS = {
    "hardware_db": "db/hardware_case_mvp.db",
    "asset_db": "db/hardware_asset.db",
    "workbench_runtime_db": "db/workbench_runtime.db",
    "sources": "sources",
    "audit": "audit",
    "backups": "backups",
    "rebuildable": "rebuildable",
}

BOOTSTRAP_REQUIRED_FIELDS = frozenset(
    {
        "contract_version",
        "installation_id",
        "persistent_data_root",
        "data_layout_version",
        "created_at",
    }
)
MANIFEST_REQUIRED_FIELDS = frozenset(
    {
        "contract_version",
        "installation_id",
        "data_layout_version",
        "hardware_schema_version",
        "asset_schema_version",
        "workbench_schema_version",
        "relative_paths",
        "initialized_at",
        "last_successful_app_version",
        "last_successful_startup_at",
        "last_migration_id",
        "last_backup_id",
    }
)

LEGACY_MARKERS = (
    "hardware_case_mvp.db",
    "hardware_case_mvp_r1_workbench.db",
    "hardware_case_mvp_sources",
    "hardware_case_sources",
)


@dataclass(frozen=True)
class HardwareDataRootResolution:
    classification: str
    data_root: Path | None
    bootstrap_path: Path
    manifest_path: Path | None = None
    error_code: str | None = None

    @property
    def blocked(self) -> bool:
        return self.classification == "BLOCKED"

    def as_dict(self) -> dict[str, str | None]:
        return {
            "classification": self.classification,
            "data_root": str(self.data_root) if self.data_root else None,
            "bootstrap_path": str(self.bootstrap_path),
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "error_code": self.error_code,
        }


def _absolute(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _platform_locations(
    *,
    environment: Mapping[str, str],
    home: Path,
    platform_name: str,
) -> tuple[Path, Path]:
    if platform_name.startswith("win"):
        base = Path(
            environment.get("LOCALAPPDATA")
            or environment.get("APPDATA")
            or home / "AppData" / "Local"
        ) / "HardwareKnowledge"
        return base / BOOTSTRAP_FILENAME, base / "Data"
    if platform_name == "darwin":
        base = home / "Library" / "Application Support" / "HardwareKnowledge"
        return base / BOOTSTRAP_FILENAME, base / "Data"
    config_home = Path(environment.get("XDG_CONFIG_HOME") or home / ".config")
    data_home = Path(environment.get("XDG_DATA_HOME") or home / ".local" / "share")
    return (
        config_home / "HardwareKnowledge" / BOOTSTRAP_FILENAME,
        data_home / "HardwareKnowledge",
    )


class HardwareDataRootResolver:
    """Classify an installation without causing filesystem side effects."""

    def __init__(
        self,
        application_root: str | Path,
        *,
        environment: Mapping[str, str] | None = None,
        bootstrap_path: str | Path | None = None,
        legacy_roots: tuple[str | Path, ...] | list[str | Path] | None = None,
        home: str | Path | None = None,
        platform_name: str | None = None,
    ) -> None:
        self.application_root = _absolute(application_root)
        self.environment = dict(os.environ if environment is None else environment)
        user_home = _absolute(home) if home is not None else _absolute(Path.home())
        default_bootstrap, default_data_root = _platform_locations(
            environment=self.environment,
            home=user_home,
            platform_name=platform_name or sys.platform,
        )
        self.bootstrap_path = _absolute(bootstrap_path or default_bootstrap)
        self.default_data_root = _absolute(default_data_root)
        self.legacy_roots = (
            tuple(_absolute(path) for path in legacy_roots)
            if legacy_roots is not None
            else (self.application_root / "data",)
        )

    def resolve(self) -> HardwareDataRootResolution:
        configured = str(self.environment.get("HARDWARE_DATA_ROOT") or "").strip()
        configured_root = _absolute(configured) if configured else None
        bootstrap_exists = self.bootstrap_path.is_file()

        if bootstrap_exists:
            return self._resolve_existing_bootstrap(configured_root)

        data_root = configured_root or self.default_data_root
        if self._invalid_root_location(data_root):
            return self._blocked("PERSISTENT_DATA_ROOT_OVERLAPS_APPLICATION_ROOT", data_root)
        if self._invalid_bootstrap_location(data_root):
            return self._blocked("BOOTSTRAP_LOCATION_INVALID", data_root)

        legacy = self._legacy_candidates(data_root)
        if len(legacy) > 1:
            return self._blocked("LEGACY_LAYOUT_AMBIGUOUS", None)
        if legacy:
            return HardwareDataRootResolution(
                "LEGACY_UPGRADE_REQUIRED",
                legacy[0],
                self.bootstrap_path,
                error_code="LEGACY_UPGRADE_REQUIRED",
            )

        if not data_root.exists():
            return HardwareDataRootResolution("FIRST_INSTALL", data_root, self.bootstrap_path)
        if not data_root.is_dir():
            return self._blocked("PERSISTENT_DATA_ROOT_NOT_DIRECTORY", data_root)
        try:
            is_empty = next(data_root.iterdir(), None) is None
        except OSError:
            return self._blocked("PERSISTENT_DATA_ROOT_UNREADABLE", data_root)
        if is_empty:
            return HardwareDataRootResolution("FIRST_INSTALL", data_root, self.bootstrap_path)
        return self._blocked("UNMANAGED_DATA_ROOT_DETECTED", data_root)

    def _resolve_existing_bootstrap(
        self, configured_root: Path | None
    ) -> HardwareDataRootResolution:
        try:
            bootstrap = self._read_object(self.bootstrap_path)
        except (OSError, ValueError):
            return self._blocked("HARDWARE_BOOTSTRAP_INVALID", None)
        if not BOOTSTRAP_REQUIRED_FIELDS.issubset(bootstrap):
            return self._blocked("HARDWARE_BOOTSTRAP_INVALID", None)
        if bootstrap.get("contract_version") != BOOTSTRAP_CONTRACT_VERSION:
            return self._blocked("HARDWARE_BOOTSTRAP_CONTRACT_UNSUPPORTED", None)
        if not self._valid_nonempty_string(bootstrap.get("installation_id")):
            return self._blocked("HARDWARE_BOOTSTRAP_INVALID", None)
        if not self._valid_nonempty_string(bootstrap.get("created_at")):
            return self._blocked("HARDWARE_BOOTSTRAP_INVALID", None)
        if not self._valid_version(bootstrap.get("data_layout_version")) or (
            bootstrap.get("data_layout_version") != DATA_LAYOUT_VERSION
        ):
            return self._blocked("HARDWARE_DATA_LAYOUT_UNSUPPORTED", None)
        raw_root = bootstrap.get("persistent_data_root")
        if not isinstance(raw_root, str) or not raw_root.strip():
            return self._blocked("HARDWARE_BOOTSTRAP_INVALID", None)
        candidate_root = Path(raw_root).expanduser()
        if not candidate_root.is_absolute():
            return self._blocked("HARDWARE_BOOTSTRAP_INVALID", None)
        data_root = _absolute(candidate_root)
        if configured_root is not None and configured_root != data_root:
            return self._blocked("HARDWARE_DATA_ROOT_CONFIG_CONFLICT", data_root)
        if self._invalid_root_location(data_root):
            return self._blocked("PERSISTENT_DATA_ROOT_OVERLAPS_APPLICATION_ROOT", data_root)
        if self._invalid_bootstrap_location(data_root):
            return self._blocked("BOOTSTRAP_LOCATION_INVALID", data_root)
        if not data_root.is_dir():
            return self._blocked("HARDWARE_DATA_ROOT_MISSING", data_root)

        manifest_path = data_root / MANIFEST_RELATIVE_PATH
        if not manifest_path.is_file():
            return self._blocked("HARDWARE_DATA_MANIFEST_MISSING", data_root)
        try:
            manifest = self._read_object(manifest_path)
        except (OSError, ValueError):
            return self._blocked("HARDWARE_DATA_MANIFEST_INVALID", data_root)
        if not MANIFEST_REQUIRED_FIELDS.issubset(manifest):
            return self._blocked("HARDWARE_DATA_MANIFEST_INVALID", data_root)
        if manifest.get("contract_version") != MANIFEST_CONTRACT_VERSION:
            return self._blocked("HARDWARE_DATA_MANIFEST_CONTRACT_UNSUPPORTED", data_root)
        if manifest.get("installation_id") != bootstrap.get("installation_id"):
            return self._blocked("INSTALLATION_ID_MISMATCH", data_root)
        if manifest.get("data_layout_version") != bootstrap.get("data_layout_version"):
            return self._blocked("HARDWARE_DATA_LAYOUT_MISMATCH", data_root)
        relative_paths = manifest.get("relative_paths")
        if not self._valid_relative_paths(relative_paths):
            return self._blocked("HARDWARE_DATA_MANIFEST_INVALID", data_root)
        versions = (
            manifest.get("hardware_schema_version"),
            manifest.get("asset_schema_version"),
            manifest.get("workbench_schema_version"),
        )
        if any(not self._valid_version(version, allow_zero=True) for version in versions):
            return self._blocked("HARDWARE_DATA_MANIFEST_INVALID", data_root)
        if not self._valid_nonempty_string(manifest.get("initialized_at")):
            return self._blocked("HARDWARE_DATA_MANIFEST_INVALID", data_root)
        for key in (
            "last_successful_app_version",
            "last_successful_startup_at",
            "last_migration_id",
            "last_backup_id",
        ):
            value = manifest.get(key)
            if value is not None and not self._valid_nonempty_string(value):
                return self._blocked("HARDWARE_DATA_MANIFEST_INVALID", data_root)
        return HardwareDataRootResolution(
            "EXISTING_INSTALL",
            data_root,
            self.bootstrap_path,
            manifest_path=manifest_path,
        )

    def _legacy_candidates(self, data_root: Path) -> list[Path]:
        candidates: list[Path] = []
        for root in (*self.legacy_roots, data_root):
            resolved = _absolute(root)
            if resolved not in candidates and self._has_legacy_layout(resolved):
                candidates.append(resolved)
        return candidates

    @staticmethod
    def _has_legacy_layout(root: Path) -> bool:
        if not root.is_dir():
            return False
        return any(
            (root / marker).is_file() or (root / marker).is_dir()
            for marker in LEGACY_MARKERS
        )

    def _invalid_root_location(self, data_root: Path) -> bool:
        return _is_within(data_root, self.application_root) or _is_within(
            self.application_root, data_root
        )

    def _invalid_bootstrap_location(self, data_root: Path) -> bool:
        return _is_within(self.bootstrap_path, self.application_root) or _is_within(
            self.bootstrap_path, data_root
        )

    @staticmethod
    def _read_object(path: Path) -> dict[str, object]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value

    @staticmethod
    def _valid_nonempty_string(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())

    @staticmethod
    def _valid_version(value: object, *, allow_zero: bool = False) -> bool:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and (value >= 0 if allow_zero else value > 0)
        )

    @staticmethod
    def _valid_relative_paths(value: object) -> bool:
        if not isinstance(value, dict):
            return False
        if any(not isinstance(key, str) or not key for key in value):
            return False
        for path_value in value.values():
            if not isinstance(path_value, str) or not path_value.strip() or "\\" in path_value:
                return False
            path = PurePosixPath(path_value)
            if path.is_absolute() or ".." in path.parts:
                return False
        return all(
            value.get(key) == expected
            for key, expected in PERSISTENT_DATA_RELATIVE_PATHS.items()
        )

    def _blocked(
        self, error_code: str, data_root: Path | None
    ) -> HardwareDataRootResolution:
        return HardwareDataRootResolution(
            "BLOCKED", data_root, self.bootstrap_path, error_code=error_code
        )
