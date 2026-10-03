from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import sys

import pytest

from services.hardware_data_root import (
    BOOTSTRAP_CONTRACT_VERSION,
    DATA_LAYOUT_VERSION,
    MANIFEST_CONTRACT_VERSION,
    MANIFEST_RELATIVE_PATH,
    HardwareDataRootResolver,
)


def _write_existing_install(
    base: Path,
    *,
    bootstrap_id: str = "install-1",
    manifest_id: str = "install-1",
) -> tuple[Path, Path, Path]:
    app_root = base / "app"
    app_root.mkdir(parents=True, exist_ok=True)
    config_root = base / "stable-config"
    config_root.mkdir(parents=True, exist_ok=True)
    data_root = base / "persistent-data"
    manifest_path = data_root / MANIFEST_RELATIVE_PATH
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "contract_version": MANIFEST_CONTRACT_VERSION,
                "installation_id": manifest_id,
                "data_layout_version": DATA_LAYOUT_VERSION,
                "hardware_schema_version": 2,
                "asset_schema_version": 0,
                "workbench_schema_version": 0,
                "relative_paths": {
                    "hardware_db": "db/hardware_case_mvp.db",
                    "asset_db": "db/hardware_asset.db",
                    "workbench_runtime_db": "db/workbench_runtime.db",
                    "sources": "sources",
                    "audit": "audit",
                    "backups": "backups",
                    "rebuildable": "rebuildable",
                },
                "initialized_at": "2026-10-04T00:00:00Z",
                "last_successful_app_version": None,
                "last_successful_startup_at": None,
                "last_migration_id": None,
                "last_backup_id": None,
            }
        ),
        encoding="utf-8",
    )
    bootstrap = config_root / "bootstrap.json"
    bootstrap.write_text(
        json.dumps(
            {
                "contract_version": BOOTSTRAP_CONTRACT_VERSION,
                "installation_id": bootstrap_id,
                "persistent_data_root": str(data_root),
                "data_layout_version": DATA_LAYOUT_VERSION,
                "created_at": "2026-10-04T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    return app_root, bootstrap, data_root


def test_first_install_is_pure_read_and_keeps_data_outside_program_root(tmp_path):
    app_root = tmp_path / "application"
    app_root.mkdir()
    data_root = tmp_path / "persistent-data"
    bootstrap = tmp_path / "stable-config" / "bootstrap.json"

    result = HardwareDataRootResolver(
        app_root,
        environment={"HARDWARE_DATA_ROOT": str(data_root)},
        bootstrap_path=bootstrap,
    ).resolve()

    assert result.classification == "FIRST_INSTALL"
    assert result.data_root == data_root
    assert not data_root.exists()
    assert not bootstrap.parent.exists()
    assert not (app_root / "data").exists()


def test_valid_bootstrap_and_manifest_resolve_same_existing_install(tmp_path):
    app_root, bootstrap, data_root = _write_existing_install(tmp_path)

    result = HardwareDataRootResolver(
        app_root,
        environment={},
        bootstrap_path=bootstrap,
        legacy_roots=(),
    ).resolve()

    assert result.classification == "EXISTING_INSTALL"
    assert result.data_root == data_root
    assert result.manifest_path == data_root / MANIFEST_RELATIVE_PATH
    assert result.error_code is None


@pytest.mark.parametrize(
    "marker",
    [
        "hardware_case_mvp.db",
        "hardware_case_mvp_r1_workbench.db",
        "hardware_case_mvp_sources",
        "hardware_case_sources",
    ],
)
def test_known_legacy_layout_is_not_treated_as_first_install(tmp_path, marker):
    app_root = tmp_path / "application"
    legacy_root = app_root / "data"
    legacy_root.mkdir(parents=True)
    marker_path = legacy_root / marker
    if marker.endswith("sources"):
        marker_path.mkdir()
    else:
        marker_path.touch()

    result = HardwareDataRootResolver(
        app_root,
        environment={"HARDWARE_DATA_ROOT": str(tmp_path / "new-data")},
        bootstrap_path=tmp_path / "stable-config" / "bootstrap.json",
    ).resolve()

    assert result.classification == "LEGACY_UPGRADE_REQUIRED"
    assert result.data_root == legacy_root.resolve()
    assert result.error_code == "LEGACY_UPGRADE_REQUIRED"


def test_multiple_legacy_roots_fail_closed(tmp_path):
    app_root = tmp_path / "application"
    first = app_root / "data"
    second = tmp_path / "old-install"
    first.mkdir(parents=True)
    second.mkdir()
    (first / "hardware_case_mvp.db").touch()
    (second / "hardware_case_mvp_sources").mkdir()

    result = HardwareDataRootResolver(
        app_root,
        environment={"HARDWARE_DATA_ROOT": str(tmp_path / "new-data")},
        bootstrap_path=tmp_path / "stable-config" / "bootstrap.json",
        legacy_roots=(first, second),
    ).resolve()

    assert result.classification == "BLOCKED"
    assert result.error_code == "LEGACY_LAYOUT_AMBIGUOUS"


def test_unknown_nonempty_data_root_fails_closed_without_mutation(tmp_path):
    app_root = tmp_path / "application"
    app_root.mkdir()
    data_root = tmp_path / "persistent-data"
    data_root.mkdir()
    unknown = data_root / "user-content.bin"
    unknown.write_bytes(b"preserve")
    bootstrap = tmp_path / "stable-config" / "bootstrap.json"

    result = HardwareDataRootResolver(
        app_root,
        environment={"HARDWARE_DATA_ROOT": str(data_root)},
        bootstrap_path=bootstrap,
    ).resolve()

    assert result.classification == "BLOCKED"
    assert result.error_code == "UNMANAGED_DATA_ROOT_DETECTED"
    assert unknown.read_bytes() == b"preserve"
    assert not bootstrap.exists()


@pytest.mark.parametrize(
    ("manifest_id", "configured_root", "expected_error"),
    [
        ("other-install", None, "INSTALLATION_ID_MISMATCH"),
        ("install-1", "other-root", "HARDWARE_DATA_ROOT_CONFIG_CONFLICT"),
    ],
)
def test_bootstrap_identity_and_config_conflicts_fail_closed(
    tmp_path, manifest_id, configured_root, expected_error
):
    app_root, bootstrap, data_root = _write_existing_install(
        tmp_path, manifest_id=manifest_id
    )
    env = (
        {"HARDWARE_DATA_ROOT": str(tmp_path / configured_root)}
        if configured_root
        else {}
    )

    result = HardwareDataRootResolver(
        app_root,
        environment=env,
        bootstrap_path=bootstrap,
        legacy_roots=(),
    ).resolve()

    assert result.classification == "BLOCKED"
    assert result.error_code == expected_error
    assert data_root.is_dir()


def test_existing_bootstrap_with_missing_root_or_manifest_blocks(tmp_path):
    app_root = tmp_path / "application"
    app_root.mkdir()
    config_root = tmp_path / "stable-config"
    config_root.mkdir()
    missing_root = tmp_path / "missing-data"
    bootstrap = config_root / "bootstrap.json"
    bootstrap.write_text(
        json.dumps(
            {
                "contract_version": BOOTSTRAP_CONTRACT_VERSION,
                "installation_id": "install-1",
                "persistent_data_root": str(missing_root),
                "data_layout_version": DATA_LAYOUT_VERSION,
                "created_at": "2026-10-04T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    result = HardwareDataRootResolver(
        app_root, environment={}, bootstrap_path=bootstrap
    ).resolve()
    assert result.error_code == "HARDWARE_DATA_ROOT_MISSING"

    missing_root.mkdir()
    result = HardwareDataRootResolver(
        app_root, environment={}, bootstrap_path=bootstrap
    ).resolve()
    assert result.error_code == "HARDWARE_DATA_MANIFEST_MISSING"
    assert result.classification == "BLOCKED"


def test_manifest_rejects_paths_outside_persistent_root(tmp_path):
    app_root, bootstrap, data_root = _write_existing_install(tmp_path)
    manifest_path = data_root / MANIFEST_RELATIVE_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["relative_paths"]["sources"] = "../../application/data"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = HardwareDataRootResolver(
        app_root,
        environment={},
        bootstrap_path=bootstrap,
        legacy_roots=(),
    ).resolve()

    assert result.classification == "BLOCKED"
    assert result.error_code == "HARDWARE_DATA_MANIFEST_INVALID"


def test_bootstrap_and_data_root_must_be_outside_application_root(tmp_path):
    app_root = tmp_path / "application"
    app_root.mkdir()
    data_root = app_root / "persistent-data"

    result = HardwareDataRootResolver(
        app_root,
        environment={"HARDWARE_DATA_ROOT": str(data_root)},
        bootstrap_path=tmp_path / "stable-config" / "bootstrap.json",
    ).resolve()

    assert result.classification == "BLOCKED"
    assert result.error_code == "PERSISTENT_DATA_ROOT_OVERLAPS_APPLICATION_ROOT"


def test_production_launcher_blocks_before_store_construction(tmp_path, monkeypatch):
    launcher_path = Path(__file__).resolve().parents[1] / "scripts" / "hardware_case_web_start.py"
    spec = importlib.util.spec_from_file_location("hardware_case_web_start_a1", launcher_path)
    assert spec and spec.loader
    launcher = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = launcher
    spec.loader.exec_module(launcher)
    resolution = launcher.HardwareDataRootResolution(
        "FIRST_INSTALL", tmp_path / "persistent-data", tmp_path / "bootstrap.json"
    )

    class Resolver:
        def __init__(self, application_root):
            self.application_root = application_root

        def resolve(self):
            return resolution

    monkeypatch.setattr(launcher, "HardwareDataRootResolver", Resolver)
    class BlockedCoordinator:
        def __init__(self, *args, **kwargs):
            pass

        def run(self):
            return {
                "status": "BLOCKED",
                "phase": "VERIFY_LAYOUT",
                "ready": False,
                "error_code": "HARDWARE_DATA_ROOT_MISSING",
                "data_root": str(resolution.data_root),
            }

    created = []
    monkeypatch.setattr(launcher, "HardwareStartupCoordinator", BlockedCoordinator)
    monkeypatch.setattr(
        launcher,
        "build_app",
        lambda **kwargs: created.append(kwargs) or object(),
    )
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, "argv", [str(launcher_path)])

    assert launcher.main() == 3
    assert len(created) == 1
    assert created[0]["startup_status"]["ready"] is False
