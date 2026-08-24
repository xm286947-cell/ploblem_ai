from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class WorkspaceError(RuntimeError):
    """Raised when the runtime workspace is incomplete or invalid."""


@dataclass(frozen=True)
class WorkspacePaths:
    input_dir: Path
    output_dir: Path
    knowledge_dir: Path
    logs_dir: Path
    temp_dir: Path
    reports_dir: Path


class WorkspaceManager:
    """Create and validate the directories required by the one-click runner."""

    def __init__(self, project_root: str | Path, config: Mapping[str, Any]) -> None:
        self.project_root = Path(project_root).resolve()
        workspace_cfg = config.get("workspace") or {}
        self.paths = WorkspacePaths(
            input_dir=self._path(workspace_cfg.get("input", "input")),
            output_dir=self._path(workspace_cfg.get("output", "output")),
            knowledge_dir=self._path(workspace_cfg.get("knowledge", "knowledge")),
            logs_dir=self._path(workspace_cfg.get("logs", "output/logs")),
            temp_dir=self._path(workspace_cfg.get("temp", "workspace/temp")),
            reports_dir=self._path(workspace_cfg.get("reports", "output/reports")),
        )

    def initialize(self) -> WorkspacePaths:
        required = (
            self.paths.input_dir,
            self.paths.output_dir,
            self.paths.knowledge_dir,
            self.paths.logs_dir,
            self.paths.temp_dir,
            self.paths.reports_dir,
            self.paths.input_dir / "reports",
        )
        for path in required:
            path.mkdir(parents=True, exist_ok=True)
        return self.paths

    def knowledge_status(self) -> dict[str, Any]:
        standard_case_dir = self.paths.knowledge_dir / "standard_case"
        enriched_case_dir = self.paths.knowledge_dir / "enriched_case"
        retrieval_docs_dir = self.paths.knowledge_dir / "retrieval_docs"
        cases = self._count_json(enriched_case_dir) or self._count_json(standard_case_dir)
        retrieval_docs = self._count_json(retrieval_docs_dir)
        ready = cases > 0 and retrieval_docs > 0
        return {
            "ready": ready,
            "case_count": cases,
            "retrieval_doc_count": retrieval_docs,
            "standard_case_dir": str(standard_case_dir),
            "enriched_case_dir": str(enriched_case_dir),
            "retrieval_docs_dir": str(retrieval_docs_dir),
        }

    def assert_knowledge_ready(self) -> dict[str, Any]:
        status = self.knowledge_status()
        if not status["ready"]:
            raise WorkspaceError(
                "Knowledge 未初始化。请先执行 python main.py run-all --with-index，"
                "或把已构建的 knowledge 目录复制到当前工程。"
            )
        return status

    def _path(self, value: str | Path) -> Path:
        path = Path(str(value)).expanduser()
        return path.resolve() if path.is_absolute() else (self.project_root / path).resolve()

    @staticmethod
    def _count_json(path: Path) -> int:
        if not path.exists():
            return 0
        return sum(1 for item in path.rglob("*.json") if item.is_file())
