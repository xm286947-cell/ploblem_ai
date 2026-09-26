from fastapi import FastAPI
from fastapi.testclient import TestClient

from storage_impact import (
    ConfirmedFact, FormalKnowledgeRelease, InMemoryFormalKnowledgeReleaseConsumer,
    SoftwareImpactAnalysisRequest, SoftwareImpactAnalysisStatus, SoftwareImpactEngine,
    ValidationItem, create_software_impact_router,
)


def release():
    return FormalKnowledgeRelease(
        release_id="K-TOPIC-1", release_version="1", semantic_scope="TBW P-E Percentage Used eMMC PRE_EOL",
        trigger_fact_names=["tbw", "pe", "percentage_used", "emmc", "pre_eol"],
        technical_meaning="Formal storage fact has a bounded engineering meaning.",
        software_impact=["Review software write policy and test coverage."],
        design_concern=["Preserve compatibility until validation completes."],
        monitoring_impact=["Monitor the affected metric."],
        validation_items=[ValidationItem(validation_id="V-1", title="Golden validation", description="Run the golden vector.", evidence_refs=["validation-evidence"])],
        evidence_refs=["knowledge-evidence"],
    )


def test_consumer_adapter_and_status_layers():
    consumer = InMemoryFormalKnowledgeReleaseConsumer([release()])
    facts = [ConfirmedFact(fact_id=f"f-{name}", metric_name=name, value=1, evidence_refs=[f"e-{name}"]) for name in ["tbw", "pe", "percentage_used", "emmc", "pre_eol"]]
    result = SoftwareImpactEngine(consumer).analyze(SoftwareImpactAnalysisRequest(device_id="d1", usage_context={"product": "storage"}, requested_topics=["TBW"], confirmed_facts=facts))
    assert result.status is SoftwareImpactAnalysisStatus.EVIDENCED
    assert result.schema_version == "1.1"
    assert result.impacts[0].validation_items[0].validation_id == "V-1"
    assert "knowledge-evidence" in result.impacts[0].evidence_refs
    assert "validation-evidence" in result.impacts[0].evidence_refs


def test_missing_knowledge_and_missing_fact_are_distinct():
    request = SoftwareImpactAnalysisRequest(device_id="d1", confirmed_facts=[])
    assert SoftwareImpactEngine().analyze(request).status is SoftwareImpactAnalysisStatus.INSUFFICIENT_KNOWLEDGE
    result = SoftwareImpactEngine().analyze(SoftwareImpactAnalysisRequest(device_id="d1", confirmed_facts=[ConfirmedFact(fact_id="f", metric_name="tbw", value=1, evidence_refs=["fact-e"])], formal_knowledge_releases=[release()]))
    assert result.status is SoftwareImpactAnalysisStatus.INSUFFICIENT_FACT


def test_two_formal_software_impact_apis_are_present():
    app = FastAPI(); app.include_router(create_software_impact_router()); client = TestClient(app)
    payload = SoftwareImpactAnalysisRequest(device_id="d1", confirmed_facts=[]).model_dump(mode="json")
    created = client.post("/storage/software-impact/analyze", json=payload)
    assert created.status_code == 201
    assert client.get(f"/storage/software-impact/{created.json()['analysis_id']}").status_code == 200
