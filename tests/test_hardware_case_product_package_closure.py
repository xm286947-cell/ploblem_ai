from __future__ import annotations

from pathlib import Path

from scripts import build_hardware_case_product_test_package as package_builder


def test_all_asset_migration_modules_are_package_allowlisted_and_closure_roots():
    modules = package_builder.asset_migration_module_paths(package_builder.ROOT)
    assert "services/hardware_asset_migrations/*.py" in package_builder.INCLUDE_GLOBS
    assert package_builder.REQUIRED_ASSET_MIGRATION_MODULES.issubset(set(modules))

    closure = package_builder.dependency_closure(package_builder.ROOT)

    assert closure["status"] == "PASS"
    assert set(modules).issubset(set(closure["files"]))
    assert set(modules).issubset(set(closure["roots"]))
    assert all("\\" not in path for path in closure["files"])
    assert all("\\" not in edge["from"] and "\\" not in edge["target"] for edge in closure["edges"])


def test_new_asset_migration_module_is_discovered_automatically(tmp_path: Path, monkeypatch):
    migration_dir = tmp_path / "services" / "hardware_asset_migrations"
    migration_dir.mkdir(parents=True)
    for name in (
        "__init__.py",
        "v001_candidate_repository.py",
        "v002_legacy_migration.py",
        "v003_source_operation_journal.py",
        "v004_future_migration.py",
    ):
        (migration_dir / name).write_text('"""fixture"""\n', encoding="utf-8")
    monkeypatch.setattr(package_builder, "CLOSURE_ROOTS", [])

    closure = package_builder.dependency_closure(tmp_path)

    future = "services/hardware_asset_migrations/v004_future_migration.py"
    assert closure["status"] == "PASS"
    assert future in closure["asset_migration_modules"]
    assert future in closure["roots"]
    assert future in closure["files"]
