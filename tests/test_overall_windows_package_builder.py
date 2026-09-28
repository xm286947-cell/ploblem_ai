from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_overall_vnext_windows_trial.py"


def test_windows_trial_builder_binds_full_package_and_checksums_to_current_dut(tmp_path):
    completed = subprocess.run(
        [sys.executable, str(BUILDER), "--output-dir", str(tmp_path)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    values = dict(line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line)
    package_dir = Path(values["PACKAGE_DIR"])
    package_zip = Path(values["PACKAGE_ZIP"])
    commit = values["DUT_SOURCE_COMMIT"]

    manifest = json.loads((package_dir / "OVERALL_VNEXT_WINDOWS_TRIAL_MANIFEST.json").read_text())
    assert manifest["dut_source_commit"] == commit
    assert manifest["runtime_binding"]["runtime_source"] == "runtime/__init__.py"
    assert manifest["dependency_install"]["wheelhouse_included"] is False
    assert (package_dir / "RUNTIME_COMMIT").read_text().strip() == commit
    assert (package_dir / "START_OVERALL_VNEXT_WINDOWS.bat").is_file()
    assert (package_dir / "scripts/overall_vnext_windows_start.py").is_file()
    assert not (package_dir / "vendor/unified_agent_runtime/runtime/__init__.py").exists()

    for line in (package_dir / "SHA256SUMS.txt").read_text().splitlines():
        expected, relative = line.split("  ", 1)
        assert hashlib.sha256((package_dir / relative).read_bytes()).hexdigest() == expected
    with zipfile.ZipFile(package_zip) as bundle:
        assert bundle.testzip() is None
        members = bundle.namelist()
        assert not any("/.git/" in name for name in members)
        assert any(name.endswith("/START_OVERALL_VNEXT_WINDOWS.bat") for name in members)
    assert hashlib.sha256(package_zip.read_bytes()).hexdigest() == values["PACKAGE_SHA256"]
