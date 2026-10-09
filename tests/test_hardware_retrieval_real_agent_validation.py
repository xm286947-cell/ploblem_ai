from __future__ import annotations

import json
from pathlib import Path

from services.hardware_retrieval_tagger import build_tagger_input
from tools.hardware_retrieval_real_agent_validation import main


def test_real_agent_harness_requires_explicit_authorization_before_runtime(
    tmp_path: Path,
    capsys,
) -> None:
    input_path = tmp_path / "missing-input.json"
    runtime_constructed = False

    def runtime_factory():
        nonlocal runtime_constructed
        runtime_constructed = True
        raise AssertionError("runtime/provider must not be constructed")

    result = main(["--input", str(input_path)], runtime_factory=runtime_factory)

    assert result == 2
    assert runtime_constructed is False
    assert "REAL_PROVIDER_EXECUTION_NOT_AUTHORIZED" in capsys.readouterr().err


def test_manual_real_agent_fixtures_are_three_valid_synthetic_projections() -> None:
    fixture_path = (
        Path(__file__).parent
        / "fixtures"
        / "hardware_retrieval_real_agent_cases.json"
    )
    projections = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert len(projections) == 3
    assert [row["case_label"] for row in projections] == [
        "CASE-01-SYNTHETIC-MCU-RESET",
        "CASE-02-SYNTHETIC-CAN-INTERRUPTION",
        "CASE-03-SYNTHETIC-LDO-OSCILLATION",
    ]
    assert all(build_tagger_input(row)["knowledge_id"].startswith("SYNTHETIC-") for row in projections)
