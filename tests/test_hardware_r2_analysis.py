"""Evidence-scoped assistant tests; fake Agent is not a real Provider call."""
from services.hardware_r2_analysis import HardwareR2EngineeringConsumptionService


class FormalKnowledge:
    def get(self, knowledge_id):
        if knowledge_id != "KO-207":
            return None
        return {
            "knowledge_id": "KO-207",
            "business_case_id": "A0207",
            "engineering_rule": "模拟量输出精度依赖参考源电压精度",
            "design_constraint": "需考虑运放offset和LDO离散性",
            "verification_method": "测量REF电压及模拟量输出误差",
            "root_cause": "参考源电路不准",
            "evidence_refs": ["EV207"],
        }


def test_analysis_uses_existing_formal_field_and_citation():
    def stub(plan):
        assert plan["intent"] == "DESIGN_REUSE"
        return (
            {"selected_fields": ["engineering_rule", "design_constraint"], "unknowns": []},
            {"task_id": "UNIT_ONLY"},
        )
    result = HardwareR2EngineeringConsumptionService(
        FormalKnowledge(), agent=stub
    ).analyze(knowledge_id="KO-207", intent="DESIGN_REUSE")
    assert result["status"] == "COMPLETED"
    assert result["recommendations"][0]["text"] == "模拟量输出精度依赖参考源电压精度"
    assert result["recommendations"][1]["source"]["field"] == "design_constraint"
    assert result["recommendations"][1]["source"]["evidence_scope"] == "KNOWLEDGE_LEVEL"


def test_agent_cannot_use_nonexistent_source_field():
    svc = HardwareR2EngineeringConsumptionService(
        FormalKnowledge(),
        agent=lambda _: (
            {"selected_fields": ["imaginary_source"], "unknowns": []}, {}
        ),
    )
    result = svc.analyze(knowledge_id="KO-207", intent="DESIGN_REUSE")
    assert result["status"] == "REJECTED"
    assert result["recommendations"] == []


def test_provider_unavailable_is_not_claimed_as_agent_success():
    result = HardwareR2EngineeringConsumptionService(
        FormalKnowledge()
    ).analyze(knowledge_id="KO-207", intent="DESIGN_REUSE")
    assert result["status"] == "BLOCKED"
    assert result["recommendations"] == []


def test_invalid_intent_and_nonexistent_knowledge_fail_closed():
    svc = HardwareR2EngineeringConsumptionService(FormalKnowledge())
    try:
        svc.analyze(knowledge_id="KO-207", intent="ADMIN_ACTION")
    except ValueError:
        pass
    else:
        assert False
    try:
        svc.analyze(knowledge_id="UNKNOWN", intent="DESIGN_REUSE")
    except LookupError:
        pass
    else:
        assert False
