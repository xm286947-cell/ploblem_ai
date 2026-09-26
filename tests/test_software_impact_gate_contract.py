from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from products.storage_rc1.storage_life.software_impact import (
    ConfirmedFact, FormalKnowledgeRelease, InMemoryFormalKnowledgeReleaseConsumer,
    SoftwareImpactAnalysisRequest, SoftwareImpactAnalysisStatus, SoftwareImpactEngine,
    ValidationItem, create_software_impact_router,
    ValidationItemType, ValidationItemStatus,
)


def release():
    return FormalKnowledgeRelease(
        release_id="K-TOPIC-1", release_version="1", semantic_scope="TBW P-E Percentage Used eMMC PRE_EOL",
        trigger_fact_names=["tbw", "pe", "percentage_used", "emmc", "pre_eol"],
        technical_meaning="Formal storage fact has a bounded engineering meaning.",
        software_impact=["Review software write policy and test coverage."],
        design_concern=["Preserve compatibility until validation completes."],
        monitoring_impact=["Monitor the affected metric."],
        validation_items=[ValidationItem(validation_id="V-1", trigger_ref="tbw", type=ValidationItemType.TEST, title="Golden validation", description="Run the golden vector.", status=ValidationItemStatus.PENDING, evidence_refs=["validation-evidence"])],
        evidence_refs=["knowledge-evidence"],
    )


def test_consumer_adapter_and_status_layers():
    consumer = InMemoryFormalKnowledgeReleaseConsumer([release()])
    facts = [ConfirmedFact(fact_id=f"f-{name}", metric_name=name, value=1, evidence_refs=[f"e-{name}"]) for name in ["tbw", "pe", "percentage_used", "emmc", "pre_eol"]]
    result = SoftwareImpactEngine(consumer).analyze(SoftwareImpactAnalysisRequest(request_id="req-golden", device_id="d1", usage_context={"product": "storage"}, requested_topics=["TBW"], confirmed_facts=facts))
    assert result.status is SoftwareImpactAnalysisStatus.EVIDENCED
    assert result.schema_version == "1.1"
    assert result.impacts[0].validation_items[0].validation_id == "V-1"
    assert "knowledge-evidence" in result.impacts[0].evidence_refs
    assert "validation-evidence" in result.impacts[0].evidence_refs


def test_missing_knowledge_and_missing_fact_are_distinct():
    request = SoftwareImpactAnalysisRequest(request_id="req-missing", device_id="d1", confirmed_facts=[])
    assert SoftwareImpactEngine().analyze(request).status is SoftwareImpactAnalysisStatus.INSUFFICIENT_KNOWLEDGE
    result = SoftwareImpactEngine().analyze(SoftwareImpactAnalysisRequest(request_id="req-fact", device_id="d1", confirmed_facts=[ConfirmedFact(fact_id="f", metric_name="tbw", value=1, evidence_refs=["fact-e"])], formal_knowledge_releases=[release()]))
    assert result.status is SoftwareImpactAnalysisStatus.INSUFFICIENT_FACT


def test_two_formal_software_impact_apis_are_present():
    app = FastAPI(); app.include_router(create_software_impact_router()); client = TestClient(app)
    payload = SoftwareImpactAnalysisRequest(request_id="req-api", device_id="d1", confirmed_facts=[]).model_dump(mode="json")
    created = client.post("/storage/software-impact/analyze", json=payload)
    assert created.status_code == 201
    assert client.get(f"/storage/software-impact/{created.json()['analysis_id']}").status_code == 200


def _golden(topic, fact_name, value=1):
    knowledge = release()
    knowledge.semantic_scope = topic
    knowledge.trigger_fact_names = [fact_name]
    knowledge.validation_items[0].trigger_ref = fact_name
    request = SoftwareImpactAnalysisRequest(
        request_id=f"golden-{topic}", device_id="d1", requested_topics=[topic],
        confirmed_facts=[ConfirmedFact(fact_id=f"fact-{fact_name}", metric_name=fact_name, value=value, evidence_refs=[f"evidence-{fact_name}"])],
        formal_knowledge_releases=[knowledge],
    )
    return SoftwareImpactEngine().analyze(request)


def test_golden_tbw():
    assert _golden("TBW", "tbw").status is SoftwareImpactAnalysisStatus.EVIDENCED


def test_golden_pe_endurance():
    assert _golden("P/E", "pe_cycles", 1000).status is SoftwareImpactAnalysisStatus.EVIDENCED


def test_golden_nvme_percentage_used_does_not_become_remaining_life():
    result = _golden("Percentage Used", "percentage_used", 35)
    assert result.status is SoftwareImpactAnalysisStatus.EVIDENCED
    assert "remaining" not in result.impacts[0].technical_meaning.lower()


def test_golden_emmc_life_time_ab():
    assert _golden("eMMC Life Time A/B", "emmc_life_time_a").status is SoftwareImpactAnalysisStatus.EVIDENCED


def test_golden_pre_eol():
    assert _golden("PRE_EOL", "pre_eol").status is SoftwareImpactAnalysisStatus.EVIDENCED


def test_golden_missing_formal_knowledge_is_not_a_definitive_impact():
    result = SoftwareImpactAnalysisRequest(request_id="golden-missing", device_id="d1", requested_topics=["TBW"], confirmed_facts=[ConfirmedFact(fact_id="f", metric_name="tbw", value=1, evidence_refs=["fact-e"])])
    analyzed = SoftwareImpactEngine().analyze(result)
    assert analyzed.status is SoftwareImpactAnalysisStatus.INSUFFICIENT_KNOWLEDGE
    assert analyzed.impacts == []


def test_request_id_and_not_applicable_contract():
    with pytest.raises(ValidationError):
        SoftwareImpactAnalysisRequest(device_id="d1")
    result = SoftwareImpactEngine().analyze(SoftwareImpactAnalysisRequest(request_id="na", device_id="d1", usage_context={"not_applicable": True}))
    assert result.status is SoftwareImpactAnalysisStatus.NOT_APPLICABLE


def test_validation_item_enums_and_orphan_conclusion_are_closed():
    item = ValidationItem(validation_id="v", trigger_ref="tbw", type=ValidationItemType.MONITORING, description="Monitor", status=ValidationItemStatus.CONFIRMED)
    assert item.type is ValidationItemType.MONITORING
    orphan_release = release(); orphan_release.trigger_fact_names = []
    result = SoftwareImpactEngine().analyze(SoftwareImpactAnalysisRequest(request_id="orphan", device_id="d1", formal_knowledge_releases=[orphan_release]))
    assert result.impacts == []
    assert result.status is SoftwareImpactAnalysisStatus.INSUFFICIENT_FACT
