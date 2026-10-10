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


def test_candidate_packages_local_nonprod_knowledge_binding_without_old_install():
    closure = package_builder.dependency_closure(package_builder.ROOT)

    assert closure["status"] == "PASS"
    binding = "services/hardware_r1_e2e_nonprod_knowledge.py"
    assert binding in closure["roots"]
    assert binding in closure["files"]
    assert "knowledge_production/public_service.py" in closure["files"]
    assert "knowledge_production/release.py" in closure["files"]
    assert not closure["unresolved_local_imports"]

    # The Candidate reuses the existing Knowledge facade and artifact store;
    # this is the same repository implementation, not a second store.
    assert "repositories/json_repository.py" in closure["files"]
    assert package_builder.CLOSURE_ROOTS.count(binding) == 1


def test_ai_retrieval_definition_is_packaged_but_provider_config_is_preserved():
    assert (
        "config/runtime/agents/hardware_retrieval.tag.yaml"
        in package_builder.INCLUDE_FILES
    )
    assert (
        "prompts/runtime/hardware_retrieval/tagger_v1.md"
        in package_builder.INCLUDE_FILES
    )
    assert "config/hardware_search.example.yaml" in package_builder.INCLUDE_FILES
    assert "config/runtime/model.local.yaml" not in package_builder.INCLUDE_FILES


def test_r2_query_and_engineering_consumer_runtime_assets_ship_in_native_package():
    """Do not claim R2 real-provider readiness with missing packaged agents."""
    required = {
        "config/runtime/agents/hardware_retrieval.query_understand.yaml",
        "config/runtime/agents/hardware_retrieval.engineering_consumption.yaml",
        "prompts/runtime/hardware_retrieval/query_understand_v1.md",
        "prompts/runtime/hardware_retrieval/engineering_consumption_v1.md",
        "tools/hardware_r2_real_gate_probe.py",
        "docs/product/HARDWARE_R2_INTERNAL_AGENT_SECURITY_AND_TRIAL.md",
    }
    assert required.issubset(set(package_builder.INCLUDE_FILES))
    for relative in required:
        assert (package_builder.ROOT / relative).is_file(), relative
    assert "config/runtime/model.local.yaml" not in package_builder.INCLUDE_FILES
    assert all(package_builder.allowed(Path(path)) for path in required)


def test_windows_package_batch_line_endings_are_crlf_and_byte_preserving(tmp_path: Path):
    """Protect CMD parsing: LF-only .bat can drop the first command character."""
    launcher = tmp_path / "START_HARDWARE_CASE.bat"
    precheck = tmp_path / "CHECK_ENV.bat"
    untouched = tmp_path / "README.txt"
    launcher_source = b"@echo off\nsetlocal\ncall CHECK_ENV.bat web\nif errorlevel 1 exit /b 2\n"
    precheck_source = b"@echo off\r\nsetlocal\ncall INIT_LOCAL_CONFIG.bat\r\n"
    launcher.write_bytes(launcher_source)
    precheck.write_bytes(precheck_source)
    untouched.write_bytes(b"this file stays LF-only\n")

    normalized = package_builder.normalize_windows_batch_line_endings(tmp_path)

    assert normalized == ["CHECK_ENV.bat", "START_HARDWARE_CASE.bat"]
    assert launcher.read_bytes() == launcher_source.replace(b"\n", b"\r\n")
    assert precheck.read_bytes() == b"@echo off\r\nsetlocal\r\ncall INIT_LOCAL_CONFIG.bat\r\n"
    assert untouched.read_bytes() == b"this file stays LF-only\n"
    assert package_builder.normalize_windows_batch_line_endings(tmp_path) == []
