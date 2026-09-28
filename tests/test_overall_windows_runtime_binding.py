from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "overall_vnext_windows_start.py"


def _package_root(path: Path, *, commit: str = "a" * 40) -> Path:
    (path / "runtime").mkdir(parents=True)
    (path / "config/runtime").mkdir(parents=True)
    (path / "tools/openai_mock").mkdir(parents=True)
    (path / "runtime/__init__.py").write_text("PACKAGE_RUNTIME = True\n", encoding="utf-8")
    (path / "config/runtime/model.yaml").write_text("active_model: test\nmodels: {}\n", encoding="utf-8")
    (path / "tools/openai_mock/server.py").write_text("", encoding="utf-8")
    (path / "RUNTIME_COMMIT").write_text(commit, encoding="utf-8")
    (path / "OVERALL_VNEXT_WINDOWS_TRIAL_MANIFEST.json").write_text(json.dumps({
        "package_type": "WINDOWS_MANUAL_TRIAL_PACKAGE", "dut_source_commit": commit,
    }), encoding="utf-8")
    return path


def _run_runtime_check(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LAUNCHER), "--package-root", str(path), "--check-runtime-only"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )


def test_windows_launcher_binds_only_to_manifest_pinned_shared_runtime(tmp_path):
    package = _package_root(tmp_path / "package")
    checked = _run_runtime_check(package)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "RUNTIME_BINDING=PASS" in checked.stdout
    assert f"RUNTIME_ACTUAL_SOURCE={package / 'runtime/__init__.py'}" in checked.stdout

    (package / "vendor/unified_agent_runtime/runtime").mkdir(parents=True)
    (package / "vendor/unified_agent_runtime/runtime/__init__.py").write_text("", encoding="utf-8")
    duplicated = _run_runtime_check(package)
    assert duplicated.returncode != 0
    assert "DUPLICATE_VENDOR_RUNTIME_PRESENT" in duplicated.stderr


def test_windows_launcher_rejects_manifest_runtime_commit_mismatch(tmp_path):
    package = _package_root(tmp_path / "package")
    (package / "RUNTIME_COMMIT").write_text("b" * 40, encoding="utf-8")
    checked = _run_runtime_check(package)
    assert checked.returncode != 0
    assert "OVERALL_VNEXT_DUT_COMMIT_MISMATCH" in checked.stderr


def test_root_windows_entrypoint_uses_the_single_package_runtime():
    batch = (ROOT / "START_OVERALL_VNEXT_WINDOWS.bat").read_text(encoding="utf-8")
    assert 'set "UNIFIED_AGENT_RUNTIME_ROOT=%PACKAGE_ROOT%"' in batch
    assert 'set "PYTHONPATH=%PACKAGE_ROOT%"' in batch
    assert "vendor\\unified_agent_runtime" not in batch
    assert "--check-runtime-only" in batch
