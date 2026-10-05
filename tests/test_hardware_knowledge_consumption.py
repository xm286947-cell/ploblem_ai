from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality_knowledge.web.hardware_knowledge_consumption_api import (
    PUBLIC_PREFIX,
    create_hardware_knowledge_consumption_router,
)
from services.hardware_knowledge_consumption import (
    CONSUMPTION_CONTRACT_VERSION,
    HardwareKnowledgeConsumptionError,
    HardwareKnowledgeConsumptionProjectionStore,
    HardwareKnowledgeConsumptionService,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (ROOT / "schema/hardware_knowledge_consumption_v1.schema.json").read_text(
        encoding="utf-8"
    )
)
BUSINESS_CASE_ID = "HC-CONSUMPTION-1"
ASSET_CANDIDATE_ID = "HCAND-CONSUMPTION-1"
KNOWLEDGE_ID = "KNOWLEDGE-CONSUMPTION-1"
PUBLIC_REF = f"HC-KNOWLEDGE-{BUSINESS_CASE_ID}-R1"
SOURCE_ID = "a" * 64


def _formal_object() -> dict[str, Any]:
    return {
        "contract_version": "knowledge-object/v1",
        "knowledge_id": KNOWLEDGE_ID,
        "domain": "HARDWARE_CASE",
        "object_type": "HARDWARE_CASE",
        "content": {
            "contract_version": "hardware-case-knowledge-object/v1",
            "identity": {
                "business_case_id": BUSINESS_CASE_ID,
                "raw_title": "MCU startup reset",
            },
            "source_fact": {
                "source_id": SOURCE_ID,
                "business_case_id": BUSINESS_CASE_ID,
            },
            "engineering_context": {
                "component_or_device": {
                    "value": "MCU",
                    "evidence_block_ids": ["B0001"],
                },
                "peer_device_or_load": {
                    "value": None,
                    "evidence_block_ids": [],
                },
                "interface": {"value": "UART"},
                "signal": {"value": "3.3V TTL TX"},
                "key_parameters": [
                    {"name": "push_pull_current", "value": "20mA", "unit": "mA"}
                ],
            },
            "observed_problem": {
                "symptom": {"value": "MCU resets during startup"},
                "occurrence_condition": {"value": "under load"},
                "failure_mode": {"value": None},
            },
            "engineering_analysis": {
                "root_cause": {"value": "Weak pull-up"},
                "failure_mechanism": {"value": "Insufficient drive"},
                "analysis_process": {"value": "Compared unloaded and loaded waveform"},
            },
            "engineering_resolution": {
                "actions": {"value": "Use push-pull output"},
                "verification_result": {"value": "Long-run test passed"},
            },
            "reusable_knowledge": {
                "engineering_rule": {"value": "Check drive strength"},
                "design_constraint": {"value": "Match interface load"},
                "diagnostic_clue": {"value": "Voltage drops only under load"},
                "verification_method": {"value": "Compare waveform under load"},
                "applicability": {"value": "Digital outputs driving external loads"},
                "conclusion": {"value": "Insufficient drive causes reset"},
            },
            "evidence": [{"block_id": "B0001", "text": "explicit evidence"}],
        },
        "candidate_ref": PUBLIC_REF,
        "evidence_refs": ["EV-CONSUMPTION-1"],
        "revision": 1,
        "status": "ACTIVE",
    }


def _promotion(**overrides: Any) -> dict[str, Any]:
    return {
        "asset_candidate_id": ASSET_CANDIDATE_ID,
        "business_case_id": BUSINESS_CASE_ID,
        "source_id": SOURCE_ID,
        "promotion_status": "VERIFIED",
        "knowledge_id": KNOWLEDGE_ID,
        "public_ref": PUBLIC_REF,
        **overrides,
    }


def _candidate(**overrides: Any) -> dict[str, Any]:
    return {
        "candidate_id": ASSET_CANDIDATE_ID,
        "business_case_id": BUSINESS_CASE_ID,
        "source_id": SOURCE_ID,
        "promotion_status": "VERIFIED",
        "evidence_refs": [
            {
                "evidence_id": "EV-CONSUMPTION-1",
                "block_id": "B0001",
            }
        ],
        **overrides,
    }


class FakeAssets:
    def __init__(self, promotions=None, candidates=None):
        self.promotions = promotions or [_promotion()]
        self.candidates = candidates or {ASSET_CANDIDATE_ID: _candidate()}

    def list_promotion_records(self):
        return [dict(item) for item in self.promotions]

    def get_promotion_record(self, candidate_id):
        return next(
            (
                dict(item)
                for item in self.promotions
                if item["asset_candidate_id"] == candidate_id
            ),
            None,
        )

    def get_candidate(self, candidate_id):
        value = self.candidates.get(candidate_id)
        return None if value is None else dict(value)


class FakeAdapter:
    def __init__(self, value=None, error=None):
        self.value = value or _formal_object()
        self.error = error
        self.calls = []

    def get_object(self, knowledge_id, *, expected_revision=None):
        self.calls.append((knowledge_id, expected_revision))
        if self.error:
            raise self.error
        return dict(self.value)


def _service(tmp_path, *, promotions=None, adapter=None, candidates=None):
    store = HardwareKnowledgeConsumptionProjectionStore(
        tmp_path / "rebuildable" / "hardware_knowledge_consumption.db"
    )
    service = HardwareKnowledgeConsumptionService(
        store,
        candidate_repository=FakeAssets(promotions, candidates),
        knowledge_adapter=adapter or FakeAdapter(),
    )
    return service, store


def _validate(value):
    jsonschema.validate(value, SCHEMA)


def test_rebuild_projects_only_verified_formal_object_and_preserves_identity(tmp_path):
    adapter = FakeAdapter()
    service, store = _service(tmp_path, adapter=adapter)

    result = service.rebuild_all_verified()
    item = service.get(KNOWLEDGE_ID)

    assert result["eligible_promotion_count"] == 1
    assert result["projected_count"] == 1
    assert result["projection_status"]["status"] == "READY"
    assert adapter.calls == [(KNOWLEDGE_ID, 1)]
    assert item["contract_version"] == CONSUMPTION_CONTRACT_VERSION
    assert item["public_ref"] == PUBLIC_REF
    assert item["business_case_id"] == BUSINESS_CASE_ID
    assert item["title"] == "MCU startup reset"
    assert item["symptom"] == "MCU resets during startup"
    assert item["root_cause"] == "Weak pull-up"
    assert item["interface"] == "UART"
    assert item["key_parameters"] == [
        {"name": "push_pull_current", "value": "20mA", "unit": "mA"}
    ]
    assert item["device_refs"] == [
        {
            "category": None,
            "generic_name_or_series": "MCU",
            "internal_material_no": None,
            "manufacturer": None,
            "manufacturer_part_no": None,
            "evidence_refs": [],
            "status": "EXPLICIT",
        }
    ]
    assert item["evidence_refs"] == ["EV-CONSUMPTION-1"]
    assert item["source_domain"] == "HARDWARE_CASE"
    assert item["source_object_type"] == "HARDWARE_CASE"
    assert item["formal_revision"] == 1
    assert item["formal_status"] == "ACTIVE"
    assert len(item["formal_object_hash"]) == 64
    _validate(item)
    assert len(store.list_all()) == 1


def test_rebuild_is_idempotent_and_failure_preserves_previous_projection(tmp_path):
    adapter = FakeAdapter()
    service, store = _service(tmp_path, adapter=adapter)
    service.rebuild_all_verified()
    before = store.get(KNOWLEDGE_ID)

    service.rebuild_all_verified()
    after = store.get(KNOWLEDGE_ID)
    assert len(store.list_all()) == 1
    assert after["formal_object_hash"] == before["formal_object_hash"]
    assert after["projected_at"] >= before["projected_at"]

    adapter.error = OSError("knowledge contract unavailable")
    with pytest.raises(HardwareKnowledgeConsumptionError):
        service.rebuild_all_verified()
    assert store.get(KNOWLEDGE_ID) == after


def test_project_verified_refreshes_one_record_idempotently(tmp_path):
    service, store = _service(tmp_path)

    first = service.project_verified(ASSET_CANDIDATE_ID)
    second = service.project_verified(ASSET_CANDIDATE_ID)

    assert first["projected"] is True
    assert second["knowledge_id"] == KNOWLEDGE_ID
    assert len(store.list_all()) == 1
    assert store.projection_status()["row_count"] == 1


def test_incomplete_or_nonverified_promotions_are_not_projected(tmp_path):
    service, store = _service(
        tmp_path,
        promotions=[
            _promotion(promotion_status="PUBLISHED_PENDING_QUERY_BACK"),
            _promotion(
                asset_candidate_id="HCAND-INCOMPLETE",
                knowledge_id=None,
                public_ref=None,
            ),
        ],
    )
    result = service.rebuild_all_verified()
    assert result["eligible_promotion_count"] == 0
    assert result["skipped_incomplete_verified_count"] == 1
    assert store.list_all() == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(knowledge_id="wrong-id"),
        lambda value: value.update(candidate_ref="wrong-ref"),
        lambda value: value.update(revision=2),
        lambda value: value.update(domain="OTHER"),
        lambda value: value.update(evidence_refs=["other-evidence"]),
        lambda value: value["content"]["identity"].update(
            business_case_id="wrong-case"
        ),
    ],
)
def test_formal_identity_mismatch_fails_closed(tmp_path, mutation):
    formal = _formal_object()
    mutation(formal)
    service, store = _service(tmp_path, adapter=FakeAdapter(formal))
    with pytest.raises(HardwareKnowledgeConsumptionError):
        service.rebuild_all_verified()
    assert store.projection_status()["status"] == "MISSING"


def test_normalized_substring_weighted_retrieval_and_explainable_reasons(tmp_path):
    service, store = _service(tmp_path)
    service.rebuild_all_verified()

    result = service.search("　ＭＣＵ  ", source_domain="hardware_case")
    item = result["results"][0]
    assert item["match_score"] == 250  # title 100 + symptom 90 + DeviceRef 60
    assert item["match_reasons"] == [
        {
            "matched_field": "title",
            "match_type": "SUBSTRING",
            "matched_text": "mcu",
            "weight": 100,
        },
        {
            "matched_field": "symptom",
            "match_type": "SUBSTRING",
            "matched_text": "mcu",
            "weight": 90,
        },
        {
            "matched_field": "device_refs",
            "match_type": "SUBSTRING",
            "matched_text": "mcu",
            "weight": 60,
        },
    ]
    _validate(result)

    filtered = service.search(
        "",
        business_case_id=BUSINESS_CASE_ID,
        interface=" uart ",
        device="mcu",
    )
    assert len(filtered["results"]) == 1
    assert filtered["results"][0]["match_score"] == 0
    assert filtered["results"][0]["match_reasons"] == []


def test_multi_keyword_search_matches_across_weighted_fields_and_requires_all_terms(tmp_path):
    service, _store = _service(tmp_path)
    service.rebuild_all_verified()

    result = service.search("  ＭＣＵ   UART  ")
    assert len(result["results"]) == 1
    item = result["results"][0]
    assert item["match_score"] == 315  # title 100 + symptom 90 + interface 65 + device 60
    assert item["match_reasons"] == [
        {
            "matched_field": "title",
            "match_type": "SUBSTRING",
            "matched_text": "mcu",
            "weight": 100,
        },
        {
            "matched_field": "symptom",
            "match_type": "SUBSTRING",
            "matched_text": "mcu",
            "weight": 90,
        },
        {
            "matched_field": "interface",
            "match_type": "SUBSTRING",
            "matched_text": "uart",
            "weight": 65,
        },
        {
            "matched_field": "device_refs",
            "match_type": "SUBSTRING",
            "matched_text": "mcu",
            "weight": 60,
        },
    ]
    _validate(result)

    assert service.search("MCU UART definitely-absent")["results"] == []


def test_scene_ranking_prioritizes_relevant_fields_without_changing_match_score(tmp_path):
    service, store = _service(tmp_path)
    service.rebuild_all_verified()
    base = store.get(KNOWLEDGE_ID)

    title_hit = dict(base)
    title_hit.update(
        knowledge_id="KNOWLEDGE-TITLE-HIT",
        public_ref="HC-KNOWLEDGE-TITLE-HIT-R1",
        business_case_id="HC-TITLE-HIT",
        title="Thermal issue",
        symptom="Unrelated symptom",
    )
    symptom_hit = dict(base)
    symptom_hit.update(
        knowledge_id="KNOWLEDGE-SYMPTOM-HIT",
        public_ref="HC-KNOWLEDGE-SYMPTOM-HIT-R1",
        business_case_id="HC-SYMPTOM-HIT",
        title="Unrelated title",
        symptom="Thermal issue under load",
    )
    store.replace_all([title_hit, symptom_hit])

    default = service.search("thermal")
    assert [item["knowledge_id"] for item in default["results"]] == [
        "KNOWLEDGE-TITLE-HIT",
        "KNOWLEDGE-SYMPTOM-HIT",
    ]
    assert [item["match_score"] for item in default["results"]] == [100, 90]

    market = service.search("thermal", scene="MARKET_ISSUE")
    assert [item["knowledge_id"] for item in market["results"]] == [
        "KNOWLEDGE-SYMPTOM-HIT",
        "KNOWLEDGE-TITLE-HIT",
    ]
    assert {
        item["knowledge_id"]: item["match_score"] for item in market["results"]
    } == {
        "KNOWLEDGE-TITLE-HIT": 100,
        "KNOWLEDGE-SYMPTOM-HIT": 90,
    }

    rnd = service.search("thermal", scene="rnd_diagnosis")
    assert rnd["results"][0]["knowledge_id"] == "KNOWLEDGE-SYMPTOM-HIT"

    device = service.search("thermal", scene="DEVICE_RISK")
    assert device["results"][0]["knowledge_id"] == "KNOWLEDGE-TITLE-HIT"

    with pytest.raises(HardwareKnowledgeConsumptionError) as error:
        service.search("thermal", scene="UNKNOWN_SCENE")
    assert error.value.code == "SEARCH_SCENE_INVALID"


def test_search_order_is_stable_by_score_then_knowledge_id(tmp_path):
    service, store = _service(tmp_path)
    service.rebuild_all_verified()
    first = store.get(KNOWLEDGE_ID)
    second = dict(first)
    second.update(
        knowledge_id="KNOWLEDGE-CONSUMPTION-0",
        public_ref="HC-KNOWLEDGE-HC-CONSUMPTION-0-R1",
        business_case_id="HC-CONSUMPTION-0",
    )
    store.replace_all([first, second])

    result = service.search("reset")
    assert [item["knowledge_id"] for item in result["results"]] == [
        "KNOWLEDGE-CONSUMPTION-0",
        KNOWLEDGE_ID,
    ]
    assert result["results"][0]["match_score"] == result["results"][1]["match_score"]


def test_nonempty_search_text_excludes_nonmatching_rows_but_empty_text_keeps_them(
    tmp_path,
):
    service, store = _service(tmp_path)
    service.rebuild_all_verified()
    matching = store.get(KNOWLEDGE_ID)
    nonmatching = dict(matching)
    nonmatching.update(
        knowledge_id="KNOWLEDGE-CONSUMPTION-0",
        public_ref="HC-KNOWLEDGE-HC-CONSUMPTION-0-R1",
        business_case_id="HC-CONSUMPTION-0",
        title="Power supply drift",
        symptom="Rail oscillation under load",
        root_cause="Poor decoupling",
        failure_mechanism="Voltage ripple",
        engineering_rule="Add local capacitance",
        design_constraint="Keep supply impedance low",
        diagnostic_clue="Ripple grows with load",
        verification_method="Measure rail ripple",
        actions="Add decoupling capacitor",
        interface="SPI",
        signal="RESET_N",
        key_parameters=[],
        device_refs=[],
        occurrence_condition="During transmission",
        analysis_process="Compared rail measurements",
        verification_result="Stable after change",
        conclusion="Supply ripple caused failure",
        applicability="Low-voltage digital systems",
    )
    store.replace_all([matching, nonmatching])

    searched = service.search("mcu")
    assert [item["knowledge_id"] for item in searched["results"]] == [KNOWLEDGE_ID]
    assert searched["results"][0]["match_score"] > 0

    structured_only = service.search("")
    assert {item["knowledge_id"] for item in structured_only["results"]} == {
        KNOWLEDGE_ID,
        "KNOWLEDGE-CONSUMPTION-0",
    }
    assert all(item["match_score"] == 0 for item in structured_only["results"])


def test_nonmatching_nonempty_search_returns_no_rows(tmp_path):
    service, _store = _service(tmp_path)
    service.rebuild_all_verified()

    assert service.search("nothing matching") == {
        "contract_version": CONSUMPTION_CONTRACT_VERSION,
        "results": [],
    }


def test_search_http_scene_contract_and_invalid_scene(tmp_path):
    service, _store = _service(tmp_path)
    service.rebuild_all_verified()
    app = FastAPI()
    app.include_router(create_hardware_knowledge_consumption_router(service))
    client = TestClient(app)

    response = client.get(
        PUBLIC_PREFIX + "/search",
        params={"text": "MCU", "scene": "RND_DIAGNOSIS"},
    )
    assert response.status_code == 200
    _validate(response.json())

    invalid = client.get(
        PUBLIC_PREFIX + "/search",
        params={"text": "MCU", "scene": "NOT_A_SCENE"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "SEARCH_SCENE_INVALID"


def test_missing_projection_and_read_only_http_contract(tmp_path):
    service, store = _service(tmp_path)
    app = FastAPI()
    app.include_router(create_hardware_knowledge_consumption_router(service))
    client = TestClient(app)

    assert service.projection_status()["status"] == "MISSING"
    assert client.get(PUBLIC_PREFIX + "/search").status_code == 503
    assert client.post(PUBLIC_PREFIX + "/search", json={}).status_code == 405

    service.rebuild_all_verified()
    response = client.get(PUBLIC_PREFIX + "/objects/" + KNOWLEDGE_ID)
    assert response.status_code == 200
    _validate(response.json())
    assert client.get(PUBLIC_PREFIX + "/objects/no-such-id").status_code == 404


def test_p0_app_binds_new_contract_to_rebuildable_persistent_data_root(tmp_path):
    from quality_knowledge.web.p0_app import create_p0_app

    data_root = tmp_path / "persistent"
    db_dir = data_root / "db"
    db_dir.mkdir(parents=True)
    app = create_p0_app(
        tmp_path / "quality.db",
        hardware_case_db_path=db_dir / "hardware_case_mvp.db",
        hardware_tree_upload_dir=tmp_path / "tree-uploads",
        hardware_case_source_root=data_root / "sources",
        enabled_domains={"HARDWARE_CASE"},
    )
    expected = data_root / "rebuildable" / "hardware_knowledge_consumption.db"
    assert app.state.hardware_knowledge_consumption_service.store.db_path == expected

    client = TestClient(app)
    assert app.state.hardware_knowledge_consumption_service.projection_status()[
        "status"
    ] == "MISSING"
    assert client.get(PUBLIC_PREFIX + "/search").status_code == 503
    assert not expected.exists()
    assert client.post(PUBLIC_PREFIX + "/search", json={}).status_code == 405


def test_projection_can_rebuild_corrupt_or_incompatible_files(tmp_path):
    db = tmp_path / "rebuildable" / "projection.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"not sqlite")
    service = HardwareKnowledgeConsumptionService(
        HardwareKnowledgeConsumptionProjectionStore(db),
        candidate_repository=FakeAssets(),
        knowledge_adapter=FakeAdapter(),
    )
    assert service.projection_status()["status"] == "CORRUPT"
    result = service.rebuild_all_verified()
    assert result["projection_status"]["status"] == "READY"
    assert service.get(KNOWLEDGE_ID) is not None


def test_incompatible_projection_schema_is_detected_and_rebuilt(tmp_path):
    db = tmp_path / "rebuildable" / "projection.db"
    db.parent.mkdir(parents=True)
    connection = sqlite3.connect(db)
    try:
        connection.execute(
            "CREATE TABLE hardware_knowledge_consumption_projection "
            "(knowledge_id TEXT PRIMARY KEY)"
        )
        connection.execute(
            "CREATE TABLE hardware_knowledge_consumption_meta "
            "(singleton INTEGER PRIMARY KEY, projection_schema_version INTEGER, "
            "row_count INTEGER, last_rebuilt_at TEXT)"
        )
        connection.execute(
            "INSERT INTO hardware_knowledge_consumption_meta "
            "VALUES(1,1,0,'2026-10-04T00:00:00Z')"
        )
        connection.commit()
    finally:
        # sqlite3.Connection's context manager commits/rolls back but does not
        # close the handle. Windows cannot os.replace() an open SQLite file.
        connection.close()
    service = HardwareKnowledgeConsumptionService(
        HardwareKnowledgeConsumptionProjectionStore(db),
        candidate_repository=FakeAssets(),
        knowledge_adapter=FakeAdapter(),
    )
    assert service.projection_status()["status"] == "INCOMPATIBLE"
    assert service.rebuild_all_verified()["projection_status"]["status"] == "READY"


def test_new_contract_does_not_access_unified_knowledge_storage_directly():
    source = (ROOT / "services/hardware_knowledge_consumption.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(
        name.startswith(("knowledge_production", "repositories.json_artifact"))
        for name in imported
    )
