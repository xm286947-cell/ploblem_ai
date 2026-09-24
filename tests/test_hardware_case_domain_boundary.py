"""ARCH-AUDIT-HARDWARE-CASE-001 domain-boundary regression."""

from __future__ import annotations

import sys

from fastapi.testclient import TestClient


def test_hardware_case_only_composition_does_not_load_other_business_domains(tmp_path):
    from quality_knowledge.web.p0_app import create_p0_app

    p0_db = tmp_path / "quality_capability_p1.db"
    hardware_db = tmp_path / "hardware_case_mvp.db"

    app = create_p0_app(
        p0_db,
        hardware_case_db_path=hardware_db,
        hardware_tree_upload_dir=tmp_path / "tree_uploads",
        hardware_case_source_root=tmp_path / "sources",
        enabled_domains={"HARDWARE_CASE"},
    )

    assert app.state.enabled_domains == ("HARDWARE_CASE",)
    assert app.state.p0_repository is None
    assert app.state.repeat_risk_service is None
    assert not p0_db.exists()

    # The product-only composition must not even import these business modules.
    assert "quality_knowledge.web.repeat_risk_integration" not in sys.modules
    assert "quality_knowledge.web.api_v2" not in sys.modules
    assert "quality_knowledge.web.p1_pages" not in sys.modules
    assert "quality_knowledge.p0.repository" not in sys.modules
    assert "services.historical_case_contract" not in sys.modules
    assert "services.knowledge_service" not in sys.modules

    client = TestClient(app)
    assert client.get("/").history
    assert client.get("/p0/hardware-cases").status_code == 200
    assert client.get("/p0/hardware-cases/base-data").status_code == 200
    assert client.get("/api/v2/hardware-cases").status_code == 200

    # Other product surfaces are deliberately absent in this composition.
    assert client.get("/api/v2/products").status_code == 404
    assert client.get("/p0/issues").status_code == 404
    assert client.get("/p0/cases").status_code == 404


def test_default_composition_contract_remains_full():
    from quality_knowledge.web.p0_app import FULL_DOMAINS, _normalize_domains

    assert _normalize_domains(None) == FULL_DOMAINS
    assert _normalize_domains({"hardware_case"}) == frozenset({"HARDWARE_CASE"})
