from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

EXPECTED = "f9ca45f82960b3ce380273cf26868bc842a72b7f"
PRODUCT_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = PRODUCT_ROOT / "scripts" / "preflight.py"


def _write_runtime(root: Path, *, marker: bool = True) -> None:
    for rel in [
        "runtime/__init__.py",
        "runtime/providers/openai_compatible.py",
        "tools/openai_mock/server.py",
        "config/runtime/model.yaml",
    ]:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n" if path.suffix in {".yaml"} else "# fixture\n", encoding="utf-8")
    if marker:
        (root / "RUNTIME_COMMIT").write_text(EXPECTED + "\n", encoding="utf-8")


def _init_parent_git(parent: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=parent, check=True)
    subprocess.run(["git", "config", "user.email", "storage-r6@test.invalid"], cwd=parent, check=True)
    subprocess.run(["git", "config", "user.name", "Storage R6 Gate"], cwd=parent, check=True)
    (parent / "PARENT.txt").write_text("parent provenance must be ignored\n", encoding="utf-8")
    subprocess.run(["git", "add", "PARENT.txt"], cwd=parent, check=True)
    subprocess.run(["git", "commit", "-qm", "fake parent head"], cwd=parent, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=parent, text=True).strip()


def _run_preflight(runtime_root: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["UNIFIED_AGENT_RUNTIME_ROOT"] = str(runtime_root)
    return subprocess.run(
        [sys.executable, str(PREFLIGHT), "--runtime-root", str(runtime_root), "--mode", "mock"],
        cwd=PRODUCT_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def test_fe02_parent_git_head_is_ignored(tmp_path: Path) -> None:
    parent = tmp_path / "fake-parent"
    parent.mkdir()
    parent_head = _init_parent_git(parent)
    assert parent_head != EXPECTED

    runtime_root = parent / "storage-r6-r1" / "vendor" / "unified_agent_runtime"
    runtime_root.mkdir(parents=True)
    _write_runtime(runtime_root, marker=True)

    result = _run_preflight(runtime_root)
    assert result.returncode == 0, result.stdout
    assert f"EXPECTED_RUNTIME={EXPECTED}" in result.stdout
    assert f"ACTUAL_RUNTIME={EXPECTED}" in result.stdout
    assert "PROVENANCE_SOURCE=RUNTIME_COMMIT" in result.stdout
    assert "PARENT_GIT_HEAD_IGNORED=YES" in result.stdout
    assert parent_head not in result.stdout


def test_fe03_missing_marker_fails_closed_without_runtime_own_git(tmp_path: Path) -> None:
    parent = tmp_path / "fake-parent"
    parent.mkdir()
    parent_head = _init_parent_git(parent)

    runtime_root = parent / "storage-r6-r1" / "vendor" / "unified_agent_runtime"
    runtime_root.mkdir(parents=True)
    _write_runtime(runtime_root, marker=False)
    assert not (runtime_root / ".git").exists()

    result = _run_preflight(runtime_root)
    assert result.returncode == 2, result.stdout
    assert "Runtime 根目录缺少 RUNTIME_COMMIT / 自有 .git" in result.stdout
    assert parent_head not in result.stdout
