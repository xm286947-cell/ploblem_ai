"""ARCH-AUDIT-HARDWARE-CASE-001 domain-boundary regression."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]


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

    client = TestClient(app)
    response = client.get("/", follow_redirects=False)
    assert response.status_code in {302, 307}
    assert response.headers["location"] == "/p0/hardware-cases"
    assert client.get("/p0/hardware-cases").status_code == 200
    assert client.get("/p0/hardware-cases/base-data").status_code == 200
    assert client.get("/api/v2/hardware-cases").status_code == 200

    # Other product surfaces are deliberately absent in this composition.
    assert client.get("/api/v2/products").status_code == 404
    assert client.get("/p0/issues").status_code == 404
    assert client.get("/p0/cases").status_code == 404

    # Module-boundary evidence must be collected in a clean interpreter so the
    # result cannot depend on pytest collection/import order.
    probe = r"""
import json
import sys
from pathlib import Path
from quality_knowledge.web.p0_app import create_p0_app

root = Path(sys.argv[1])
app = create_p0_app(
    root / "quality.db",
    hardware_case_db_path=root / "hardware.db",
    hardware_tree_upload_dir=root / "tree",
    hardware_case_source_root=root / "sources",
    enabled_domains={"HARDWARE_CASE"},
)
targets = [
    "quality_knowledge.web.repeat_risk_integration",
    "quality_knowledge.web.api_v2",
    "quality_knowledge.web.p1_pages",
    "quality_knowledge.p0.repository",
    "services.historical_case_contract",
    "services.knowledge_service",
]
print(json.dumps({name: name in sys.modules for name in targets}))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, str(tmp_path / "probe")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    loaded = json.loads(result.stdout.strip())
    assert loaded == {name: False for name in loaded}


def test_default_composition_contract_remains_full():
    from quality_knowledge.web.p0_app import FULL_DOMAINS, _normalize_domains

    assert _normalize_domains(None) == FULL_DOMAINS
    assert _normalize_domains({"hardware_case"}) == frozenset({"HARDWARE_CASE"})
