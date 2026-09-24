from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "test_assets" / "storage_rc1" / "scenario_catalog.json"
FIXTURE_DIR = ROOT / "test_assets" / "storage_rc1" / "fixtures"


def _catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def _fixture_ids() -> set[str]:
    values = set()
    for path in FIXTURE_DIR.glob("M*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        values.add(payload["mock_id"])
    return values


def test_all_frozen_storage_requirements_have_system_test_mapping() -> None:
    catalog = _catalog()
    requirements = catalog["requirements"]
    assert [item["id"] for item in requirements] == [
        f"REQ-STG-{index:03d}" for index in range(1, 16)
    ]
    for item in requirements:
        assert item["system_cases"], item["id"]


def test_golden_a_b_c_are_all_present() -> None:
    catalog = _catalog()
    goldens = {item["id"]: item for item in catalog["golden_paths"]}
    assert set(goldens) == {"GOLDEN-A", "GOLDEN-B", "GOLDEN-C"}
    for item in goldens.values():
        assert item["system_cases"]


def test_all_catalog_mock_references_resolve_to_frozen_fixtures() -> None:
    catalog = _catalog()
    actual = _fixture_ids()
    referenced = set()
    for requirement in catalog["requirements"]:
        referenced.update(requirement["mock_ids"])
    for golden in catalog["golden_paths"]:
        referenced.update(golden["mock_ids"])
    assert referenced <= actual
    assert actual == {f"M{index:02d}" for index in range(1, 23)}


def test_coverage_five_states_are_forced_into_req_stg_005() -> None:
    catalog = _catalog()
    req = next(item for item in catalog["requirements"] if item["id"] == "REQ-STG-005")
    assert {"M03", "M04", "M05", "M06", "M07"} <= set(req["mock_ids"])


def test_human_governance_and_knowledge_governance_are_explicit() -> None:
    catalog = _catalog()
    by_id = {item["id"]: item for item in catalog["requirements"]}
    assert {"M08", "M09", "M18"} <= set(by_id["REQ-STG-006"]["mock_ids"])
    assert {"M19", "M20", "M21"} <= set(by_id["REQ-STG-009"]["mock_ids"])
