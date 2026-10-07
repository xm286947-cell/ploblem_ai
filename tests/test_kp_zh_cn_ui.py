from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from knowledge_production.models import (
    KnowledgeCandidate,
    KnowledgeEvaluation,
    ReviewRecord,
)
from knowledge_production.processing import KnowledgeProcessingService
from knowledge_production.web import _enum_zh, create_processing_app
from repositories import JsonArtifactRepository


def _seed_reviewed_candidate(root: Path) -> str:
    repo = JsonArtifactRepository(root)
    candidate = KnowledgeCandidate(
        candidate_id="KPC-zh-cn-ux-001",
        object_type="FACT",
        title="Write Amplification Factor",
        content="Higher WAF consumes more P/E cycles.",
        scope=["NAND Flash"],
        tags=[
            "storage-parameter:pe_cycles",
            "storage-semantic-candidate:PARAMETER_DEFINITION",
            "storage-semantic-candidate:CALCULATION_RULE",
        ],
        evidence_refs=["EVD-zh-cn-ux-001"],
        source_refs=["PKR-source@rev-1"],
        extraction_version="test",
        metadata={
            "storage_lifetime": {
                "device_type": "NAND Flash",
                "canonical_parameters": ["pe_cycles"],
                "semantic_class_status": "NEEDS_REVIEW",
                "semantic_class_candidates": [
                    "PARAMETER_DEFINITION",
                    "CALCULATION_RULE",
                ],
                "scenario_consumers": ["S1", "S2", "S3", "S4", "S5"],
            },
            "storage_source_bridge": {
                "semantic_class_candidates": [
                    "PARAMETER_DEFINITION",
                    "CALCULATION_RULE",
                    "CHANGE_IMPACT_RULE",
                ]
            },
        },
    )
    repo.save(
        f"knowledge/production/candidates/{candidate.candidate_id}.json",
        candidate.model_dump(mode="json"),
    )
    repo.save(
        "knowledge/production/evidence/EVD-zh-cn-ux-001.json",
        {
            "source": {
                "source_type": "PUBLIC",
                "source_id": "source-1",
                "revision": "rev-1",
            },
            "locator": {"type": "pdf_page", "value": "3"},
            "excerpt": "Higher WAF generates more P/E cycles.",
        },
    )

    evaluation = KnowledgeEvaluation(
        evaluation_id="KPE-zh-cn-ux-001",
        candidate_id=candidate.candidate_id,
        evidence_status="VALID",
        source_valid=True,
        contract_valid=True,
        scope_valid=True,
        candidate_complete=True,
        duplicate_status="NEW",
        conflict_status="NONE",
        publish_readiness=True,
        review_ready=True,
    )
    repo.save(
        (
            "knowledge/production/evaluations/"
            f"{candidate.candidate_id}/{evaluation.evaluation_id}.json"
        ),
        evaluation.model_dump(mode="json"),
    )

    effective = candidate.model_copy(
        update={
            "object_type": "CONCEPT",
            "title": "Write Amplification Increases NAND P/E Cycle Consumption",
            "tags": [
                "storage-parameter:pe_cycles",
                "storage-semantic:CHANGE_IMPACT_RULE",
            ],
        }
    )
    review = ReviewRecord(
        review_id="KPR-zh-cn-ux-001",
        candidate_id=candidate.candidate_id,
        action="EDIT",
        review_status="CONFIRMED",
        reviewed_by="summer",
        reviewed_at=datetime.now(timezone.utc),
        input_evaluation_id=evaluation.evaluation_id,
        effective_evaluation_id=evaluation.evaluation_id,
        effective_snapshot_ref=(
            "knowledge/production/review_snapshots/"
            f"{candidate.candidate_id}/KPR-zh-cn-ux-001.json"
        ),
        edit_fields=["object_type", "title", "tags"],
    )
    repo.save(
        review.effective_snapshot_ref,
        effective.model_dump(mode="json"),
    )
    repo.save(
        (
            "knowledge/production/reviews/"
            f"{candidate.candidate_id}/{review.review_id}.json"
        ),
        review.model_dump(mode="json"),
    )
    return candidate.candidate_id


def test_chinese_first_enum_labels_keep_raw_values():
    assert _enum_zh("CONCEPT") == "概念（CONCEPT）"
    assert _enum_zh("CHANGE_IMPACT_RULE") == "变更影响规则（CHANGE_IMPACT_RULE）"
    assert _enum_zh("CONFIRMED") == "已确认（CONFIRMED）"


def test_candidate_detail_uses_effective_review_snapshot_and_governed_options(tmp_path):
    candidate_id = _seed_reviewed_candidate(tmp_path)
    service = KnowledgeProcessingService(JsonArtifactRepository(tmp_path))

    detail = service.get_candidate_detail(candidate_id)
    assert detail["candidate"].object_type.value == "FACT"
    assert detail["effective_candidate"].object_type.value == "CONCEPT"
    assert detail["display_candidate"].object_type.value == "CONCEPT"
    assert "CHANGE_IMPACT_RULE" in detail["storage_semantic_review_options"]

    html = TestClient(create_processing_app(tmp_path)).get(
        f"/knowledge-production/candidates/{candidate_id}"
    ).text
    assert "知识生产工作台" in html
    assert "当前有效审核快照" in html
    assert "概念（CONCEPT）" in html
    assert "变更影响规则（CHANGE_IMPACT_RULE）" in html
    assert "人工审核可选类型" in html
    assert 'value="CHANGE_IMPACT_RULE"' in html
    assert "原始 Candidate 保持不可变" in html
