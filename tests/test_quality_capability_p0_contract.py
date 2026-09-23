from quality_knowledge.models.analysis_v2 import CapabilityGapV2DTO, EvidenceValueV2DTO
from quality_knowledge.taxonomy.seed import TERMS


def test_inferred_confidence_is_capped():
    assert EvidenceValueV2DTO(value="x", confidence=0.9).confidence == 0.6


def test_frozen_taxonomy_contains_dual_capability_axes():
    assert "QUALITY_METRICS" in TERMS["MANAGEMENT_CAPABILITY"]
    assert "TEST_VERIFICATION" in TERMS["ENGINEERING_CAPABILITY"]


def test_gap_has_only_new_capability_axes():
    gap = CapabilityGapV2DTO(
        capability_axis="QUALITY_ENGINEERING",
        capability_code="TEST_VERIFICATION",
    )
    assert gap.governance_scope == "PRODUCT"
