from pathlib import Path

from quality_knowledge.web.api_v2 import _project_historical_case_common_evidence


def _case_detail(evidence_id: str | None = "MJR-EVD-R2-W3-HIST") -> dict:
    return {
        "case_id": "CASE-R2-W3-001",
        "case_version": "CASE-REV-7",
        "evidence": [
            {
                "evidence_id": evidence_id,
                "source_type": "REPORT",
                "source_id": "ITR-R2-W3-001",
                "source_version": "SRC-REV-3",
                "source_ref": "REPORT:ITR-R2-W3-001@SRC-REV-3",
                "file_name": "review.docx",
                "page": 9,
                "section": "Root Cause",
                "raw_text": "根因证据原文。",
                "url": "https://example.test/source/ITR-R2-W3-001",
            }
        ],
    }


def test_w3_historical_case_evidence_maps_to_common_contract_without_store_access():
    projected = _project_historical_case_common_evidence(
        _case_detail(),
        requested_case_id="CASE-R2-W3-001",
    )
    item = projected["evidence"][0]
    common = item["common_evidence"]

    assert item["common_evidence_status"] == "AVAILABLE"
    assert common["contract_version"] == "common-evidence/v1.0"
    assert common["evidence_id"] == "MJR-EVD-R2-W3-HIST"
    assert common["producer_domain"] == "Historical Case"
    assert common["producer_object_id"] == "CASE-R2-W3-001"
    assert common["producer_object_version"] == "CASE-REV-7"
    assert common["source"] == {
        "source_type": "REPORT",
        "source_id": "ITR-R2-W3-001",
        "source_version": "SRC-REV-3",
    }
    assert common["locator"]["page"] == 9
    assert common["locator"]["section"] == "Root Cause"
    assert common["excerpt"] == "根因证据原文。"
    assert common["source_reference"] == "https://example.test/source/ITR-R2-W3-001"


def test_w3_historical_case_legacy_evidence_without_identity_fails_closed():
    projected = _project_historical_case_common_evidence(
        _case_detail(evidence_id=None),
        requested_case_id="CASE-R2-W3-001",
    )
    item = projected["evidence"][0]

    assert item["common_evidence_status"] == "UNAVAILABLE"
    assert item["common_evidence"] is None
    assert item["raw_text"] == "根因证据原文。"


def test_w3_historical_case_detail_uses_common_evidence_drawer_contract():
    root = Path(__file__).resolve().parents[1]
    script = (
        root / "quality_knowledge/web/static/p0_case_detail.js"
    ).read_text(encoding="utf-8")
    template = (
        root / "quality_knowledge/web/templates/p0_case_detail.html"
    ).read_text(encoding="utf-8")

    assert "common-evidence/v1.0" in script
    assert "/p0/overall/evidence?" in script
    assert "data-common-evidence" in script
    assert "location.pathname+location.search+location.hash" in script
    assert "打开统一 Evidence / Source" in script
    assert "r2-w3-common-evidence-v1" in template
