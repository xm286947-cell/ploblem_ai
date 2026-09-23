from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from presentation.contract.report import Report
from presentation.renderer.markdown_renderer import MarkdownRenderer


class FileReportRepository:
    """Persist presentation artifacts for one analysis query.

    The repository owns file paths and serialization. Analysis and construction
    components only provide the report object and do not write presentation
    files directly.
    """

    def __init__(self, root: Path, renderer: MarkdownRenderer | None = None) -> None:
        self.root = Path(root)
        self.renderer = renderer or MarkdownRenderer()

    def query_dir(self, query_id: str) -> Path:
        normalized = str(query_id).strip()
        if not normalized:
            raise ValueError("query_id不能为空")
        if Path(normalized).name != normalized or normalized in {".", ".."}:
            raise ValueError(f"query_id包含非法路径字符: {query_id}")
        return self.root / normalized

    def save(self, query_id: str, report: Report | Mapping[str, Any]) -> dict[str, Path]:
        target_dir = self.query_dir(query_id)
        target_dir.mkdir(parents=True, exist_ok=True)

        payload = report.to_dict() if isinstance(report, Report) else dict(report)
        json_path = target_dir / "report.json"
        markdown_path = target_dir / "report.md"

        self._write_json_atomic(json_path, payload)
        self.renderer.render_to_file(payload, markdown_path)
        return {"json": json_path, "markdown": markdown_path}

    def load(self, query_id: str) -> dict[str, Any]:
        path = self.query_dir(query_id) / "report.json"
        if not path.exists():
            raise FileNotFoundError(path)
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Report JSON根节点必须是对象: {path}")
        return value

    @staticmethod
    def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temp_path.replace(path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
