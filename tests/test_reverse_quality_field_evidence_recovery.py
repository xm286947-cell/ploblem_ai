"""No-evidence AI fields must become pending review, never invented facts."""
from types import SimpleNamespace

from quality_knowledge.reverse_quality import ReverseQualityService


def _analyse(ai_fields, evidence, *, characteristics=None):
    captured = {}
    service = object.__new__(ReverseQualityService)
    service.ai_client = None
    service.scenarios = SimpleNamespace(
        quality_models=lambda: {"product_characteristics": [
            {"label_zh": name} for name in (characteristics or [])
        ]},
        scenarios=lambda **kwargs: [],
    )
    service.repository = SimpleNamespace(
        complete_run=lambda run_id, **kwargs: captured.update(kwargs)
    )
    service.get = lambda itr: captured
    service._runtime_executor = SimpleNamespace(
        execute=lambda *args, **kwargs: SimpleNamespace(
            data={"fields": ai_fields, "lifecycle_code": "", "activity_code": "", "questions": []},
            model="CONTROLLED_TEST_PROVIDER",
        )
    )
    return service._analyse_run(
        {"canonical_itr": "ITR20260424040CS", "evidence": evidence},
        {"version_id": "TAX-TEST", "lifecycles": [], "activities": []},
        "SOURCE-HASH", "RQ-RUN", "CNC",
    )


def test_invalid_customer_experience_is_quarantined_not_produced():
    evidence = {"bundle.description": {"value": "异常掉电后参数丢失"}}
    result = _analyse({
        "customer_experience": {"value": "客户停产三天",
                                "evidence_ids": ["FAKE_AI_EVIDENCE_ID"]},
        "failure_mode": {"value": "异常掉电后参数丢失",
                         "evidence_ids": ["bundle.description"]},
    }, evidence)
    assert result["fields"]["failure_mode"]["value"] == "异常掉电后参数丢失"
    assert result["fields"]["customer_experience"]["value"] == ""
    assert result["fields"]["customer_experience"]["source_type"] == "MISSING"
    assert any(row["field_name"] == "customer_experience" and row["status"] == "PENDING"
               for row in result["missing_information"])


def test_fully_grounded_customer_experience_is_unchanged():
    result = _analyse(
        {"customer_experience": {
            "value": "停机报警", "evidence_ids": ["bundle.description"]}},
        {"bundle.description": {"value": "停机报警"}},
    )
    assert result["fields"]["customer_experience"]["value"] == "停机报警"
    assert result["fields"]["customer_experience"]["source_type"] == "FACT"
    assert result["missing_information"] == []


def test_invalid_root_cause_related_objects_and_quality_dictionary_do_not_create_facts():
    result = _analyse({
        "root_cause": {"value": "模型想象的根因", "evidence_ids": ["bundle.root"]},
        "related_objects": {"value": "未记录设备", "evidence_ids": ["bundle.description"]},
        "quality_characteristic": {"value": "自创质量特性", "evidence_ids": ["bundle.description"]},
    }, {
        "bundle.root": {"value": "已记录的其他原因", "target_field": "root_cause",
                        "source_type": "RESOLUTION", "provenance": "SOURCE_FACT"},
        "bundle.description": {"value": "系统运行时报警", "target_field": "problem_description"},
    }, characteristics=["可靠性"])
    for name in ("root_cause", "related_objects", "quality_characteristic"):
        assert result["fields"][name]["value"] == ""
        assert result["fields"][name]["evidence_ids"] == []
        assert any(item["field_name"] == name and item["status"] == "PENDING"
                   for item in result["missing_information"])
