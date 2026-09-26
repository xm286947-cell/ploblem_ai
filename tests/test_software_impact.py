import pytest

from products.storage_rc1.storage_life.software_impact import (
    ConfirmedFact,
    FormalKnowledgeRelease,
    SoftwareImpactAnalysisRequest,
    SoftwareImpactAnalysisStatus,
    SoftwareImpactEngine,
    ValidationItem,
    ValidationItemType,
)


def fact(name="write_pattern", value="small_write", evidence=True, source="CONFIRMED_DEVICE_FACT"):
    return ConfirmedFact(
        fact_id=f"fact-{name}", metric_name=name, value=value,
        evidence_refs=[f"fact-evidence-{name}"] if evidence else [], source_type=source,
    )


def release(status="RELEASED", evidence=True, triggers=None, content=True):
    return FormalKnowledgeRelease(
        release_id="K-WRITE-001",
        release_version="1.0",
        release_status=status,
        semantic_scope="storage write path",
        trigger_fact_names=triggers or ["write_pattern"],
        technical_meaning="The confirmed write pattern changes host-to-media write amplification exposure.",
        software_impact=["Review batching and write coalescing behavior." ] if content else [],
        design_concern=["Preserve write ordering and durability semantics."],
        monitoring_impact=["Monitor host writes and media writes."],
        validation_items=[ValidationItem(
            validation_id="VAL-001",
            trigger_ref="fact-write_pattern",
            type=ValidationItemType.TEST,
            title="Representative workload",
            description="Validate representative workload with Evidence.",
            evidence_refs=["validation-evidence"],
        )],
        evidence_refs=["knowledge-evidence"] if evidence else [],
    )


def test_formal_release_and_confirmed_fact_produce_traceable_impact():
    result = SoftwareImpactEngine().analyze(
        SoftwareImpactAnalysisRequest(
            request_id="req-1", device_id="dev-1",
            confirmed_facts=[fact()],
            formal_knowledge_releases=[release()],
        )
    )
    assert result.status is SoftwareImpactAnalysisStatus.EVIDENCED
    impact = result.impacts[0]
    assert impact.device_fact_refs == ["fact-write_pattern"]
    assert impact.knowledge_refs == ["K-WRITE-001"]
    assert impact.evidence_refs == ["fact-evidence-write_pattern", "knowledge-evidence", "validation-evidence"]
    assert result.decision_boundary == "NO_AUTO_REPLACEMENT_DECISION"


def test_candidate_knowledge_cannot_be_used_as_formal_conclusion():
    result = SoftwareImpactEngine().analyze(
        SoftwareImpactAnalysisRequest(
            request_id="req-2", device_id="dev-1", confirmed_facts=[fact()],
            formal_knowledge_releases=[release(status="CANDIDATE")],
        )
    )
    assert result.status is SoftwareImpactAnalysisStatus.INSUFFICIENT_KNOWLEDGE
    assert "FORMAL_KNOWLEDGE_RELEASE_WITH_EVIDENCE_REQUIRED" in result.missing_information


def test_missing_knowledge_evidence_fails_closed():
    result = SoftwareImpactEngine().analyze(
        SoftwareImpactAnalysisRequest(
            request_id="req-3", device_id="dev-1", confirmed_facts=[fact()],
            formal_knowledge_releases=[release(evidence=False)],
        )
    )
    assert result.status is SoftwareImpactAnalysisStatus.INSUFFICIENT_KNOWLEDGE


def test_missing_fact_evidence_fails_closed():
    result = SoftwareImpactEngine().analyze(
        SoftwareImpactAnalysisRequest(
            request_id="req-4", device_id="dev-1", confirmed_facts=[fact(evidence=False)],
            formal_knowledge_releases=[release()],
        )
    )
    assert result.status is SoftwareImpactAnalysisStatus.INSUFFICIENT_FACT
    assert "FACT_EVIDENCE_REQUIRED:write_pattern" in result.missing_information


def test_missing_trigger_fact_becomes_validation_gap_not_invented_impact():
    result = SoftwareImpactEngine().analyze(
        SoftwareImpactAnalysisRequest(
            request_id="req-5", device_id="dev-1", confirmed_facts=[fact("other_fact")],
            formal_knowledge_releases=[release()],
        )
    )
    assert result.status is SoftwareImpactAnalysisStatus.INSUFFICIENT_FACT
    assert result.impacts == []
    assert "K-WRITE-001:TRIGGER_FACT_REQUIRED:write_pattern" in result.missing_information


def test_missing_formal_impact_content_is_not_replaced_by_static_fallback():
    result = SoftwareImpactEngine().analyze(
        SoftwareImpactAnalysisRequest(
            request_id="req-6", device_id="dev-1", confirmed_facts=[fact()],
            formal_knowledge_releases=[release(content=False)],
        )
    )
    assert result.status is SoftwareImpactAnalysisStatus.INSUFFICIENT_KNOWLEDGE
    assert result.impacts == []
    assert "K-WRITE-001:FORMAL_IMPACT_CONTENT_REQUIRED" in result.missing_information


def test_non_confirmed_fact_source_is_rejected():
    result = SoftwareImpactEngine().analyze(
        SoftwareImpactAnalysisRequest(
            request_id="req-7", device_id="dev-1", confirmed_facts=[fact(source="AI_INFERRED")],
            formal_knowledge_releases=[release()],
        )
    )
    assert result.status is SoftwareImpactAnalysisStatus.INVALID_INPUT


def test_multiple_releases_keep_fact_knowledge_analysis_separated():
    second = release()
    second.release_id = "K-FSYNC-002"
    second.trigger_fact_names = ["fsync_mode"]
    second.technical_meaning = "Durability mode changes flush behavior."
    second.software_impact = ["Validate flush and journaling behavior."]
    result = SoftwareImpactEngine().analyze(
        SoftwareImpactAnalysisRequest(
            request_id="req-8", device_id="dev-1", confirmed_facts=[fact(), fact("fsync_mode", "journaled")],
            formal_knowledge_releases=[release(), second],
        )
    )
    assert result.status is SoftwareImpactAnalysisStatus.EVIDENCED
    assert len(result.impacts) == 2
    assert all(item.source_type == "FORMAL_KNOWLEDGE_ANALYSIS" for item in result.impacts)
