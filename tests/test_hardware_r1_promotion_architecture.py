"""Architecture guard: HardwareR1KnowledgePromotionService must not shadow methods."""
from __future__ import annotations

import ast
from pathlib import Path


def test_hardware_r1_promotion_public_methods_have_single_implementation() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "services"
        / "hardware_r1_knowledge_promotion.py"
    )
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    target = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef)
        and node.name == "HardwareR1KnowledgePromotionService"
    )
    public_operations = {
        "precheck_item",
        "intake_item",
        "review_item",
        "publish_item",
        "verify_item",
        "get_item",
        "intake_batch",
        "retry_failed_item",
        "retry_failed_batch",
        "get_batch",
    }
    counts = {
        name: sum(
            1
            for node in target.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        )
        for name in public_operations
    }
    assert counts == {name: 1 for name in public_operations}
