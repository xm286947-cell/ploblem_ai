from pathlib import Path

from common.config_loader import ConfigLoader
from common.workspace import WorkspaceManager


def test_config_loader_reads_yaml_and_resolves_path(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config/demo.yaml").write_text("paths:\n  input: input/data.xlsx\n", encoding="utf-8")
    loader = ConfigLoader(tmp_path)
    assert loader.get("demo", "paths.input") == "input/data.xlsx"
    assert loader.path("demo", "paths.input") == (tmp_path / "input/data.xlsx").resolve()


def test_workspace_initializer_creates_required_directories(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path, {"workspace": {}})
    paths = manager.initialize()
    assert paths.input_dir.is_dir()
    assert paths.output_dir.is_dir()
    assert paths.knowledge_dir.is_dir()
    assert paths.logs_dir.is_dir()
    assert paths.temp_dir.is_dir()
    assert (paths.input_dir / "reports").is_dir()
