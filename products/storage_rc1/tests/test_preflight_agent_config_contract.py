from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from storage_life import runtime_bridge


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = PRODUCT_ROOT / "scripts" / "preflight.py"
RUNTIME_COMMIT = "9e36eeb0237459b884ee0d3e663ccf5833bfc685"
CANONICAL_AGENT_CONFIGS = {
    runtime_bridge.GENERIC_AGENT_ID: Path("config/runtime/agents/storage.ai.json_call.yaml"),
    runtime_bridge.EMMC_PARAMETER_AGENT_ID: Path(
        "config/runtime/agents/storage.emmc.parameter_extract.yaml"
    ),
}
LEGACY_AGENT_CONFIGS = (
    Path("config/runtime/storage.ai.json_call.yaml"),
    Path("config/runtime/storage.emmc.parameter_extract.yaml"),
)


def _prepared_project(root: Path) -> tuple[Path, Path]:
    project = root / "products" / "storage_rc1"
    runtime = root / "vendor" / "unified_agent_runtime"
    (project / "scripts").mkdir(parents=True)
    shutil.copy2(PREFLIGHT, project / "scripts" / "preflight.py")

    for relative in (
        "storage_life/app.py",
        "storage_life/runtime_bridge.py",
        "prompts/runtime/storage/emmc_parameter_extract.md",
        "examples/synthetic_emmc.pdf",
    ):
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    for relative in CANONICAL_AGENT_CONFIGS.values():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture: true\n", encoding="utf-8")

    for relative in (
        "runtime/__init__.py",
        "runtime/providers/openai_compatible.py",
        "tools/openai_mock/server.py",
        "config/runtime/model.yaml",
    ):
        path = runtime / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture: true\n", encoding="utf-8")
    (runtime / "RUNTIME_COMMIT").write_text(RUNTIME_COMMIT + "\n", encoding="utf-8")
    return project, runtime


def _run_preflight(project: Path, runtime: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["UNIFIED_AGENT_RUNTIME_ROOT"] = str(runtime)
    return subprocess.run(
        [sys.executable, str(project / "scripts" / "preflight.py"), "--runtime-root", str(runtime), "--mode", "mock"],
        cwd=project,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def test_preflight_accepts_canonical_agent_configs_without_legacy_duplicates(tmp_path: Path) -> None:
    project, runtime = _prepared_project(tmp_path)
    result = _run_preflight(project, runtime)
    assert result.returncode == 0, result.stdout
    assert "PASS all checks" in result.stdout
    assert all((project / relative).is_file() for relative in CANONICAL_AGENT_CONFIGS.values())
    assert all(not (project / relative).exists() for relative in LEGACY_AGENT_CONFIGS)


@pytest.mark.parametrize(
    "agent_id",
    [runtime_bridge.GENERIC_AGENT_ID, runtime_bridge.EMMC_PARAMETER_AGENT_ID],
)
def test_preflight_fails_closed_when_a_canonical_agent_config_is_missing(
    tmp_path: Path, agent_id: str
) -> None:
    project, runtime = _prepared_project(tmp_path)
    missing = project / CANONICAL_AGENT_CONFIGS[agent_id]
    missing.unlink()

    result = _run_preflight(project, runtime)
    assert result.returncode == 2, result.stdout
    assert f"Storage 包缺少 {CANONICAL_AGENT_CONFIGS[agent_id]}" in result.stdout


@pytest.mark.parametrize(
    "agent_id",
    [runtime_bridge.GENERIC_AGENT_ID, runtime_bridge.EMMC_PARAMETER_AGENT_ID],
)
def test_preflight_paths_match_runtime_bridge(agent_id: str) -> None:
    from scripts import preflight

    expected = (PRODUCT_ROOT / CANONICAL_AGENT_CONFIGS[agent_id]).resolve()
    assert runtime_bridge.agent_config_path(agent_id) == expected
    assert expected in preflight.agent_config_paths(PRODUCT_ROOT)
    assert preflight.EXPECTED == runtime_bridge.RUNTIME_EXPECTED_COMMIT


def test_macos_and_windows_normal_launchers_share_windows_start_preflight_chain() -> None:
    macos = (PRODUCT_ROOT / "run_macos.sh").read_text(encoding="utf-8")
    windows = (PRODUCT_ROOT / "run_windows.bat").read_text(encoding="utf-8")
    startup = (PRODUCT_ROOT / "scripts" / "windows_start.py").read_text(encoding="utf-8")

    assert "from scripts import windows_start as startup" in macos
    assert '"%PACKAGE_ROOT%\\scripts\\windows_start.py"' in windows
    assert '"preflight.py"' in startup
    assert '"--mode"' in startup
    assert '"real"' in startup
