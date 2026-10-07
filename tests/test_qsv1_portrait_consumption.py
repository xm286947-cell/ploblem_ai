from types import SimpleNamespace

from quality_knowledge.quality_scenario_v1 import (
    QualityScenarioV1,
    ScenarioConfirmationMetadata,
    ScenarioEvidenceReference,
    ScenarioProvenanceType,
    ScenarioRelationType,
    ScenarioReviewMetadata,
    ScenarioReviewStatus,
    ScenarioSourceReference,
    ScenarioStatus,
    ScenarioTriggerSource,
    ScenarioVersionMetadata,
)
from quality_knowledge.scenario_assets import ScenarioAssets
from quality_knowledge.scenarios import ScenarioRepository


class PublishedQSV1Repo:
    def __init__(self, item):
        self.item = item
        self.statuses = []

    def list(self, *, status=None, **_kwargs):
        self.statuses.append(status)
        return [self.item] if str(status) == "PUBLISHED" else []


def _published_qsv1():
    return QualityScenarioV1(
        scenario_id="QSV1C-PORTRAIT-001",
        scenario_version=4,
        status=ScenarioStatus.PUBLISHED,
        product_code="PLC",
        product_name="PLC",
        lifecycle_stage_code="LONG_TERM_OPERATION",
        lifecycle_stage_name="长稳运行",
        business_activity_code="CONTINUOUS_PRODUCTION",
        business_activity_name="连续生产运行",
        business_goal="稳定运行",
        scenario_name="长周期运行场景",
        scenario_description="客户长期运行时关注稳定性",
        quality_concern_code="RELIABILITY",
        quality_concern_name="可靠性",
        trigger_source=ScenarioTriggerSource.HIGH_PERCEPTION,
        trigger_reason="客户高感知",
        trigger_condition="长周期运行",
        expected_result="持续稳定",
        applicability_scope="工业现场",
        source_problem_refs=[
            ScenarioSourceReference(
                source_ref="QSV1-SELECTED-ISSUE:MAT-SW-1",
                source_type="SELECTED_ISSUE",
                source_id="MAT-SW-1",
                canonical_itr="ITR2026100001",
                product_code="PLC",
                relation_type=ScenarioRelationType.SUPPORTING,
            )
        ],
        evidence_refs=[
            ScenarioEvidenceReference(
                evidence_id="EV-1",
                source_ref="QSV1-SELECTED-ISSUE:MAT-SW-1",
                evidence_type="SOURCE_FACT",
                source_text="真实来源",
                supports=["scenario_description"],
                source_type=ScenarioProvenanceType.FACT,
            )
        ],
        review=ScenarioReviewMetadata(
            review_status=ScenarioReviewStatus.CONFIRMED,
            reviewer="quality",
            reviewed_at="2026-10-07T00:00:00Z",
        ),
        confirmation=ScenarioConfirmationMetadata(
            quality_confirmed_by="quality",
            quality_confirmed_at="2026-10-07T00:00:00Z",
            technical_confirmed_by="rnd",
            technical_confirmed_at="2026-10-07T00:00:00Z",
        ),
        version=ScenarioVersionMetadata(
            created_by="test",
            created_at="2026-10-07T00:00:00Z",
            updated_at="2026-10-07T00:00:00Z",
            published_at="2026-10-07T00:00:00Z",
            parent_scenario_version=3,
            change_summary="published",
        ),
    )


def test_published_qsv1_projects_into_existing_portrait_engine(tmp_path):
    legacy = ScenarioRepository(tmp_path / "legacy.db")
    qsv1 = PublishedQSV1Repo(_published_qsv1())
    service = ScenarioAssets(legacy, qsv1_repository=qsv1)

    assets = service.catalog()
    projected = next(x for x in assets if x["scenario_id"] == "QSV1C-PORTRAIT-001")
    assert projected["source_of_truth"] == "QSV1"
    assert projected["status"] == "PUBLISHED"
    assert projected["issue_ids"] == ["MAT-SW-1", "ITR2026100001"]

    facts = {
        "MAT-SW-1": {
            "business_issue_id": "ITR2026100001",
            "industry": "新能源",
            "customer": "客户A",
            "product": "PLC-X",
            "year": "2026",
            "month": "10",
            "source_workbench": "software-operations",
        },
        "ITR2026100001": {
            "business_issue_id": "ITR2026100001",
            "industry": "新能源",
            "customer": "客户A",
            "product": "PLC-X",
            "year": "2026",
            "month": "10",
            "source_workbench": "software-operations",
        },
    }

    product = service.portrait({"product": "PLC-X"}, facts=facts, assets=assets)
    industry = service.portrait({"industry": "新能源"}, facts=facts, assets=assets)
    customer = service.portrait({"customer": "客户A"}, facts=facts, assets=assets)

    assert product["issue_count"] == 1
    assert industry["issue_count"] == 1
    assert customer["issue_count"] == 1
    assert any(row["label"] == "PLC-X" for row in product["product_portrait"])
    assert any(row["label"] == "客户A" for row in customer["customer_portrait"])
    assert qsv1.statuses == ["PUBLISHED"] * 1
