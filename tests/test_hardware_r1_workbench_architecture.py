"""Architecture guard for Hardware R1 Workbench ownership."""
from __future__ import annotations

import ast
from pathlib import Path


def test_human_review_is_owned_only_by_workbench_service() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "services"
        / "hardware_case_r1_workbench.py"
    )
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    classes = {
        node.name: node
        for node in module.body
        if isinstance(node, ast.ClassDef)
    }
    store = classes["HardwareR1WorkbenchStore"]
    service = classes["HardwareR1WorkbenchService"]

    def count_methods(node: ast.ClassDef, name: str) -> int:
        return sum(
            1
            for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == name
        )

    assert count_methods(store, "apply_human_review") == 0
    assert count_methods(service, "apply_human_review") == 1
