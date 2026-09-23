from __future__ import annotations

from pathlib import Path

from repositories.hardware_case_repository import HardwareCaseRepository
from services.hardware_case_backend import HardwareCaseBackendService
from services.hardware_case_contract import (
    BLOCK_EVIDENCE,
    BLOCK_MAPPING,
    CONTRACT_VERSION,
)


def _field(candidate, confirmed=None, disposition="UNREVIEWED", evidence_refs=()):
    return {
        "candidate_value": candidate,
        "confirmed_value": confirmed,
        "review_disposition": disposition,
        "evidence_refs": list(evidence_refs),
    }


def _case(case_id="HC-M2-1", *, status="PENDING_REVIEW", reviewed=False):
    disposition = "CONFIRMED" if reviewed else "UNREVIEWED"
    confirmed = (lambda value: value if reviewed else None)
    return {
        "contract_version": CONTRACT_VERSION,
        "case_id": case_id,
        "title": "连接器间歇接触不良",
        "case_status": status,
        "processing_status": "READY",
        "source_refs": ["word:A2001.docx"],
        "product_context": {
            "product": "控制器",
            "device_name": "Wafer",
            "device_model": "W-100",
        },
        "facts": {
            "symptom": _field("间歇掉线", confirmed("间歇掉线"), disposition),
            "root_cause": _field("端子压接不足", confirmed("端子压接不足"), disposition),
            "actions": _field("优化压接参数", confirmed("优化压接参数"), disposition),
        },
    }


def _mapping(
    *,
    mapping_id="MAP-M2-1",
    case_id="HC-M2-1",
    tree_type="MATERIAL_DEVICE",
    node_id="MAT-WAFER",
    node_path="采购件/连接器/线对板/Wafer",
    role="PRIMARY",
    status="CONFIRMED",
):
    return {
        "mapping_id": mapping_id,
        "case_id": case_id,
        "tree_type": tree_type,
        "node_id": node_id,
        "node_path": node_path,
        "relation_role": role,
        "mapping_status": status,
        "confidence": 0.9,
        "basis_refs": ["EV-M2-1"],
    }


def _evidence(*, case_id="HC-M2-1", status="AVAILABLE"):
    return {
        "evidence_id": "EV-M2-1",
        "case_id": case_id,
        "source_ref": "word:A2001.docx",
        "evidence_type": "TEXT",
        "locator": {"section": "原因分析", "paragraph": 8},
        "excerpt_or_caption": "端子压接高度不足，振动时接触电阻波动。",
        "evidence_status": status,
    }


def _backend(tmp_path: Path) -> HardwareCaseBackendService:
    return HardwareCaseBackendService(
        HardwareCaseRepository(tmp_path / "hardware_case.sqlite3")
    )


def test_m2_01_repository_initializes_versioned_schema(tmp_path: Path) -> None:
    repo = HardwareCaseRepository(tmp_path / "hardware_case.sqlite3")
    assert repo.schema_version() == 1


def test_m2_02_candidate_and_confirmed_are_persisted_separately(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.save_case(_case())
    backend.review_case(
        "HC-M2-1",
        "root_cause",
        disposition="CONFIRMED",
        confirmed_value="人工确认：端子压接不足",
    )
    stored = backend.get_case("HC-M2-1", role="MAINTAINER")
    field = stored["facts"]["root_cause"]
    assert field["candidate_value"] == "端子压接不足"
    assert field["confirmed_value"] == "人工确认：端子压接不足"


def test_m2_03_backend_publish_gate_is_authoritative(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.save_case(_case(reviewed=True))
    gate = backend.publish_case("HC-M2-1")
    assert gate["passed"] is False
    assert BLOCK_EVIDENCE in gate["blockers"]
    assert BLOCK_MAPPING in gate["blockers"]
    assert backend.get_case("HC-M2-1", role="MAINTAINER")["case_status"] == "PENDING_REVIEW"


def test_m2_04_single_confirmed_tree_mapping_can_publish(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.save_case(_case(reviewed=True))
    backend.set_mapping(_mapping())
    backend.add_evidence(_evidence())
    result = backend.publish_case("HC-M2-1")
    assert result["passed"] is True
    assert result["case_status"] == "PUBLISHED"
    assert backend.get_case("HC-M2-1")["case_status"] == "PUBLISHED"


def test_m2_05_consumer_search_never_returns_unpublished_case(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.save_case(_case())
    assert backend.search_cases("间歇掉线")["results"] == []
    maintainer = backend.search_cases(
        "间歇掉线",
        role="MAINTAINER",
        statuses=["PENDING_REVIEW"],
    )
    assert maintainer["results"][0]["case_id"] == "HC-M2-1"


def test_m2_06_tree_query_counts_only_published_for_consumer(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.save_case(_case())
    backend.set_mapping(_mapping())
    assert backend.list_cases_by_tree_node("MAT-WAFER")["case_count"] == 0
    backend.add_evidence(_evidence())
    for name, value in (
        ("symptom", "间歇掉线"),
        ("root_cause", "端子压接不足"),
        ("actions", "优化压接参数"),
    ):
        backend.review_case(
            "HC-M2-1",
            name,
            disposition="CONFIRMED",
            confirmed_value=value,
        )
    assert backend.publish_case("HC-M2-1")["passed"] is True
    assert backend.list_cases_by_tree_node("MAT-WAFER")["case_count"] == 1


def test_m2_07_deprecated_exits_default_consumer_surface(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.save_case(_case(reviewed=True))
    backend.set_mapping(_mapping())
    backend.add_evidence(_evidence())
    backend.publish_case("HC-M2-1")
    backend.deprecate_case("HC-M2-1")
    assert backend.search_cases("")["results"] == []
    assert backend.get_case("HC-M2-1", historical=True)["case_status"] == "DEPRECATED"


def test_m2_08_multiple_mappings_keep_one_case_and_one_primary_per_tree(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.save_case(_case())
    backend.set_mapping(_mapping(mapping_id="M1", node_id="N1", role="PRIMARY"))
    backend.set_mapping(_mapping(mapping_id="M2", node_id="N2", role="SECONDARY"))
    backend.set_mapping(_mapping(mapping_id="M3", node_id="N3", role="PRIMARY"))
    repo = backend.repository
    assert len(repo.list_cases()) == 1
    material = repo.list_mappings(case_id="HC-M2-1")
    primary = [item for item in material if item["relation_role"] == "PRIMARY"]
    assert [item["mapping_id"] for item in primary] == ["M3"]


def test_m2_09_variable_depth_tree_round_trips(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.save_tree_node(
        {
            "node_id": "C2",
            "tree_type": "CIRCUIT_FEATURE",
            "name": "DC/DC",
            "path": ["电源", "DC/DC"],
            "active": True,
        }
    )
    backend.save_tree_node(
        {
            "node_id": "M5",
            "tree_type": "MATERIAL_DEVICE",
            "name": "Wafer",
            "path": ["采购件", "连接器", "线对板", "信号", "Wafer"],
            "active": True,
        }
    )
    assert len(backend.get_tree("CIRCUIT_FEATURE")["nodes"][0]["path"]) == 2
    assert len(backend.get_tree("MATERIAL_DEVICE")["nodes"][0]["path"]) == 5


def test_m2_10_source_unavailable_keeps_published_case_and_creates_anomaly(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.save_case(_case(reviewed=True))
    backend.set_mapping(_mapping())
    backend.add_evidence(_evidence())
    backend.publish_case("HC-M2-1")

    backend.add_evidence(_evidence(status="SOURCE_UNAVAILABLE"))
    detail = backend.get_case("HC-M2-1")
    assert detail["case_status"] == "PUBLISHED"
    assert detail["evidence_health"] == "SOURCE_UNAVAILABLE"
    anomalies = backend.maintenance_anomalies()
    assert anomalies[0]["case_id"] == "HC-M2-1"
    assert anomalies[0]["code"] == "SOURCE_UNAVAILABLE"


def test_m2_11_persistence_survives_service_restart(tmp_path: Path) -> None:
    db = tmp_path / "hardware_case.sqlite3"
    first = HardwareCaseBackendService(HardwareCaseRepository(db))
    first.save_case(_case(reviewed=True))
    first.set_mapping(_mapping())
    first.add_evidence(_evidence())
    assert first.publish_case("HC-M2-1")["passed"] is True

    second = HardwareCaseBackendService(HardwareCaseRepository(db))
    detail = second.get_case("HC-M2-1")
    assert detail["case_status"] == "PUBLISHED"
    assert detail["facts"]["root_cause"] == "端子压接不足"


def test_m2_12_evidence_locator_round_trips_without_internal_paths(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.save_case(_case(reviewed=True))
    backend.set_mapping(_mapping())
    backend.add_evidence(_evidence())
    backend.publish_case("HC-M2-1")
    evidence = backend.get_evidence("HC-M2-1")["evidence"][0]
    assert evidence["source_ref"] == "word:A2001.docx"
    assert evidence["locator"] == {"section": "原因分析", "paragraph": 8}
    assert str(backend.repository.db_path) not in repr(evidence)
