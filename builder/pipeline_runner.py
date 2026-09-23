from __future__ import annotations

from pathlib import Path

from builder.m2_runner import run_m2
from builder.m3_runner import run_m3
from builder.m4_runner import run_m4
from builder.m5_runner import run_m5
from builder.m6_runner import run_m6


def run_all(
    project_root: str | Path,
    excel_path: str | Path | None = None,
    reports_dir: str | Path | None = None,
    with_ai: bool = False,
    mock_ai: bool = False,
    overwrite_ai: bool = False,
    with_index: bool = False,
    overwrite_index: bool = False,
) -> dict:
    root = Path(project_root).resolve()
    m2 = run_m2(root, excel_path=excel_path, reports_dir=reports_dir)
    m3 = run_m3(root)
    m4 = run_m4(root)
    result = {
        "pipeline": "M2->M3->M4",
        "m2": m2, "m3": m3, "m4": m4,
        "success": m4.get("failed_count", 0) == 0,
    }
    if with_ai or mock_ai:
        m5 = run_m5(root, mock=mock_ai, overwrite=overwrite_ai)
        result["pipeline"] += "->M5"
        result["m5"] = m5
        result["success"] = result["success"] and m5.get("failed_count", 0) == 0
    if with_index:
        m6 = run_m6(root, overwrite=overwrite_index)
        result["pipeline"] += "->M6"
        result["m6"] = m6
        result["success"] = result["success"] and m6.get("failed_count", 0) == 0
    return result
