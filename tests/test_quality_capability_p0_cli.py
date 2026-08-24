import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "main.py"), *arguments],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_p0_init_and_status_use_the_single_clean_initializer(tmp_path):
    db_path = tmp_path / "knowledge" / "quality_capability_p0.db"
    report_path = tmp_path / "output" / "initialization.json"

    initialized = run_cli(
        "knowledge-p0-init",
        "--db",
        str(db_path),
        "--report",
        str(report_path),
    )
    assert initialized.returncode == 0, initialized.stderr
    assert json.loads(initialized.stdout)["initialization_state"] == "READY"
    assert json.loads(report_path.read_text(encoding="utf-8"))["outcome"] == "APPLIED"

    status = run_cli("knowledge-p0-status", "--db", str(db_path))
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["outcome"] == "READY"


def test_p0_init_rejects_a_non_p0_target_and_writes_diagnostic(tmp_path):
    db_path = tmp_path / "legacy.db"
    db_path.write_bytes(b"legacy-validation-file")
    report_path = tmp_path / "blocked.json"

    result = run_cli(
        "knowledge-p0-init",
        "--db",
        str(db_path),
        "--report",
        str(report_path),
    )

    assert result.returncode == 4
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["outcome"] == "INITIALIZATION_BLOCKED"
    assert payload["error"] == "P0_TARGET_MUST_BE_EMPTY_OR_P0_DATABASE"
    assert db_path.read_bytes() == b"legacy-validation-file"


def test_p1_init_and_status_expose_the_new_release_entry(tmp_path):
    db_path = tmp_path / "knowledge" / "quality_capability_p1.db"

    initialized = run_cli("knowledge-p1-init", "--db", str(db_path))
    assert initialized.returncode == 0, initialized.stderr
    assert json.loads(initialized.stdout)["initialization_state"] == "READY"

    status = run_cli("knowledge-p1-status", "--db", str(db_path))
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["outcome"] == "READY"


def test_p1_start_is_the_recommended_auto_initializing_entry():
    help_result = run_cli("knowledge-p1-start", "--help")
    assert help_result.returncode == 0
    assert "--db" in help_result.stdout and "--host" in help_result.stdout and "--port" in help_result.stdout
