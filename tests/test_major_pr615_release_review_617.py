from __future__ import annotations

from pathlib import Path
import inspect

from quality_knowledge.repeat_risk.agent_analysis import RepeatAgentAnalysisService
from quality_knowledge.web.repeat_risk_integration import RepeatWebFacade


def test_repeat_runtime_data_root_isolated(tmp_path: Path) -> None:
    project = tmp_path / "readonly-product"
    data_root = tmp_path / "writable-data"
    project.mkdir()
    service = RepeatAgentAnalysisService(project, runtime_data_root=data_root)
    assert service.report_root == (data_root / "repeat_reports").resolve()
    # Runtime is intentionally not invoked: provider setup belongs to the
    # existing Runtime contract tests.  This gate fixes the persistence root.
    assert service.runtime_data_root == data_root.resolve()
    assert not (project / "data").exists()


def test_m84_external_config_controls_optional_switch(tmp_path: Path) -> None:
    project = tmp_path / "product"
    project.mkdir()
    packaged = project / "config" / "model.yaml"
    packaged.parent.mkdir(parents=True)
    packaged.write_text("repeat_decision_ai:\n  enabled: false\n", encoding="utf-8")

    external = tmp_path / "approved-runtime.yaml"
    external.write_text("repeat_decision_ai:\n  enabled: true\n", encoding="utf-8")

    default_service = RepeatAgentAnalysisService(project)
    enabled_service = RepeatAgentAnalysisService(project, model_config_path=external)
    assert default_service.decision_enabled is False
    assert enabled_service.decision_enabled is True


def test_repeat_facade_accepts_runtime_data_root_contract() -> None:
    # Keep this as a signature-level regression so product composition cannot
    # silently drop the writable root again.
    assert "runtime_data_root" in inspect.signature(RepeatWebFacade.from_project).parameters
