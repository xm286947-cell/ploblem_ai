"""Grounding gate: reject unsupported engineering recommendations/citations."""
import pytest

from services.hardware_engineering_analysis import (
    HardwareEngineeringAnalysisError,
    HardwareEngineeringAnalysisService,
)


class Formal:
    def get(self, knowledge_id):
        if knowledge_id != "KO-0207":
            return None
        return {
            "knowledge_id": "KO-0207", "business_case_id": "A0207",
            "engineering_rule": "模拟量输出精度强依赖参考源电压精度",
            "design_constraint": "应选用低offset、高精度运放",
            "verification_method": "修正参考源后测量模拟电流输出",
            "evidence_refs": ["EV-0207"],
        }


def response(source="模拟量输出精度强依赖参考源电压精度",
             ev="EV-0207", field="engineering_rule"):
    return {"data": {"summary": "仅供设计参考", "checks": [
        {"source_field": field, "source_excerpt": source,
         "evidence_id": ev, "recommendation": "设计时复核参考源电压精度"}],
        "unknowns": ["量化设计余量尚需确认"]},
        "trace": {"task_id": "t", "run_id": "r", "provider_calls": 1}}


def test_analysis_returns_reference_only_with_real_formal_evidence():
    result = HardwareEngineeringAnalysisService(Formal(), lambda _: response()).analyze(
        "KO-0207", "DESIGN_REUSE")
    assert result["kind"] == "AI_GENERATED_REFERENCE_NOT_FORMAL"
    assert result["checks"][0]["evidence_id"] == "EV-0207"
    assert result["checks"][0]["knowledge_id"] == "KO-0207"


@pytest.mark.parametrize("bad", [
    response(source="不存在的参数应为10V"),
    response(ev="EV-INVENTED"),
    response(field="root_cause"),
    {**response(), "trace": {"task_id": "fake", "run_id": "r", "provider_calls": 0}},
])
def test_analysis_fails_closed_on_unsupported_source_or_missing_runtime_trace(bad):
    with pytest.raises(HardwareEngineeringAnalysisError):
        HardwareEngineeringAnalysisService(Formal(), lambda _: bad).analyze(
            "KO-0207", "DESIGN_REUSE")


def test_analysis_blocks_unknown_knowledge_and_unconfigured_agent():
    with pytest.raises(HardwareEngineeringAnalysisError) as error:
        HardwareEngineeringAnalysisService(Formal(), None).analyze(
            "KO-OTHER", "DESIGN_REUSE")
    assert error.value.code == "KNOWLEDGE_NOT_FOUND"
    with pytest.raises(HardwareEngineeringAnalysisError) as error:
        HardwareEngineeringAnalysisService(Formal(), None).analyze(
            "KO-0207", "DESIGN_REUSE")
    assert error.value.code == "ENGINEERING_AGENT_NOT_CONFIGURED"
