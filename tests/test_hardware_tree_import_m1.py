from __future__ import annotations

from pathlib import Path

import pytest

from repositories.hardware_case_repository import HardwareCaseRepository
from repositories.hardware_tree_import_repository import HardwareTreeImportRepository
from services.hardware_case_backend import HardwareCaseBackendService
from services.hardware_tree_import_contract import HardwareTreeImportContractError
from services.hardware_tree_import_service import HardwareTreeImportService


SHA = "a" * 64


def _service(tmp_path: Path):
    repo = HardwareTreeImportRepository(tmp_path / "hardware.sqlite3")
    return HardwareTreeImportService(repo)


def _job(
    service: HardwareTreeImportService,
    job_id: str,
    tree_type: str = "CIRCUIT_FEATURE",
    filename: str = "tree.xlsx",
):
    return service.create_import(
        job_id=job_id,
        tree_type=tree_type,
        source_filename=filename,
        source_sha256=SHA,
        operator="maintainer-01",
    )


def _to_review(service: HardwareTreeImportService, job_id: str):
    service.advance(job_id, "PARSED")
    service.set_mapping_profile(
        job_id,
        {
            "sheet_name": "分类",
            "header_row": 3,
            "path_columns": ["系统", "模块", "功能", "特性", "子特性"],
            "metadata_columns": ["备注"],
            "business_key_column": "分类编码",
        },
    )
    service.advance(job_id, "VALIDATING")
    service.advance(job_id, "REVIEW_REQUIRED")


def _add(
    service: HardwareTreeImportService,
    job_id: str,
    change_id: str,
    node_id: str,
    name: str,
    path: list[str],
    *,
    parent_id: str | None = None,
    tree_type: str = "CIRCUIT_FEATURE",
    decision: str = "CONFIRMED",
):
    return service.save_change(
        job_id,
        {
            "change_id": change_id,
            "change_type": "ADD",
            "node_id": node_id,
            "business_key": f"BK-{node_id}",
            "decision": decision,
            "after": {
                "node_id": node_id,
                "tree_type": tree_type,
                "name": name,
                "parent_id": parent_id,
                "path": path,
                "active": True,
            },
        },
    )


def _apply_initial_tree(service: HardwareTreeImportService) -> str:
    _job(service, "JOB-C-001")
    _to_review(service, "JOB-C-001")
    _add(service, "JOB-C-001", "CH-C-ROOT", "C-ROOT", "电源", ["电源"])
    _add(
        service,
        "JOB-C-001",
        "CH-C-SURGE",
        "C-SURGE",
        "浪涌防护",
        ["电源", "输入保护", "浪涌防护"],
        parent_id="C-ROOT",
    )
    service.ready_to_apply("JOB-C-001")
    result = service.apply("JOB-C-001")
    return result["active_version"]["version_id"]


def test_m1_import_job_rejects_company_local_path_and_supports_dynamic_mapping(tmp_path: Path):
    service = _service(tmp_path)
    with pytest.raises(HardwareTreeImportContractError, match="SOURCE_PATH_NOT_ALLOWED"):
        _job(service, "BAD", filename=r"C:\\company\\tree.xlsx")

    job = _job(service, "JOB-1")
    assert job["import_type"] == "INITIAL_IMPORT"
    assert job["status"] == "UPLOADED"
    assert job["source_filename"] == "tree.xlsx"

    service.advance("JOB-1", "PARSED")
    mapped = service.set_mapping_profile(
        "JOB-1",
        {
            "sheet_name": "Sheet2",
            "header_row": 7,
            "path_columns": ["L1", "L2", "L3", "L4", "L5", "L6"],
            "metadata_columns": ["备注", "Owner"],
            "business_key_column": "编码",
        },
    )
    assert mapped["mapping_profile"]["path_columns"] == [
        "L1", "L2", "L3", "L4", "L5", "L6"
    ]
    assert mapped["mapping_profile"]["business_key_column"] == "编码"


def test_m1_initial_apply_creates_active_version_and_materialized_tree(tmp_path: Path):
    service = _service(tmp_path)
    version_id = _apply_initial_tree(service)

    assert version_id == "C-001"
    active = service.active_version("CIRCUIT_FEATURE")
    assert active and active["status"] == "ACTIVE"

    nodes = service.version_nodes(version_id)
    assert {node["node_id"] for node in nodes} == {"C-ROOT", "C-SURGE"}

    backend = HardwareCaseBackendService(
        HardwareCaseRepository(tmp_path / "hardware.sqlite3")
    )
    current = backend.get_tree("CIRCUIT_FEATURE")
    assert {node["node_id"] for node in current["nodes"]} == {"C-ROOT", "C-SURGE"}


def test_m1_update_creates_next_version_without_deleting_missing_excel_nodes(tmp_path: Path):
    service = _service(tmp_path)
    assert _apply_initial_tree(service) == "C-001"

    job = _job(service, "JOB-C-002")
    assert job["import_type"] == "UPDATE_IMPORT"
    assert job["current_version_id"] == "C-001"
    _to_review(service, "JOB-C-002")

    service.save_change(
        "JOB-C-002",
        {
            "change_id": "CH-RENAME",
            "change_type": "RENAME",
            "node_id": "C-SURGE",
            "business_key": "BK-C-SURGE",
            "decision": "CONFIRMED",
            "before": {
                "name": "浪涌防护",
                "path": ["电源", "输入保护", "浪涌防护"],
            },
            "after": {
                "name": "浪涌保护",
                "parent_id": "C-ROOT",
                "path": ["电源", "安全保护", "浪涌保护"],
            },
        },
    )
    _add(
        service,
        "JOB-C-002",
        "CH-ADD-EMC",
        "C-EMC",
        "EMC",
        ["电源", "EMC"],
        parent_id="C-ROOT",
    )

    service.ready_to_apply("JOB-C-002")
    result = service.apply("JOB-C-002")
    assert result["active_version"]["version_id"] == "C-002"

    current = service.repository.case_repository.list_tree_nodes("CIRCUIT_FEATURE")
    by_id = {node["node_id"]: node for node in current}
    assert set(by_id) == {"C-ROOT", "C-SURGE", "C-EMC"}
    assert by_id["C-SURGE"]["name"] == "浪涌保护"
    # C-ROOT was absent from the new ChangeSet, but omission is never deletion.
    assert by_id["C-ROOT"]["active"] is True

    with service.repository.connect() as connection:
        old = connection.execute(
            "SELECT status FROM hardware_tree_version WHERE version_id='C-001'"
        ).fetchone()
    assert old["status"] == "SUPERSEDED"


def test_m1_unresolved_conflict_blocks_ready_and_validation_issue_must_be_resolved(tmp_path: Path):
    service = _service(tmp_path)
    _job(service, "JOB-CONFLICT")
    _to_review(service, "JOB-CONFLICT")

    service.add_validation_issue(
        "JOB-CONFLICT",
        {
            "issue_id": "ISSUE-1",
            "sheet_name": "Sheet1",
            "row_number": 127,
            "column_name": "C",
            "original_value": "浪涌",
            "issue_type": "PARENT_LEVEL_MISSING",
            "suggested_action": "补齐上级分类",
        },
    )
    service.save_change(
        "JOB-CONFLICT",
        {
            "change_id": "CONFLICT-1",
            "change_type": "CONFLICT",
            "node_id": "C-X",
            "decision": "PENDING",
            "before": {"path": ["旧"]},
            "after": {"name": "新", "path": ["新"]},
        },
    )

    with pytest.raises(HardwareTreeImportContractError, match="UNRESOLVED_VALIDATION_ISSUES"):
        service.ready_to_apply("JOB-CONFLICT")

    service.resolve_validation_issue("ISSUE-1")
    with pytest.raises(HardwareTreeImportContractError, match="UNRESOLVED_CONFLICT"):
        service.ready_to_apply("JOB-CONFLICT")

    service.decide_change(
        "CONFLICT-1",
        "RESOLVED",
        resolved_after={
            "node_id": "C-X",
            "name": "新",
            "path": ["新"],
            "active": True,
        },
    )
    ready = service.ready_to_apply("JOB-CONFLICT")
    assert ready["status"] == "READY_TO_APPLY"


def test_m1_apply_failure_is_atomic_and_keeps_previous_active_version(tmp_path: Path):
    service = _service(tmp_path)
    assert _apply_initial_tree(service) == "C-001"

    _job(service, "JOB-BAD")
    _to_review(service, "JOB-BAD")
    service.save_change(
        "JOB-BAD",
        {
            "change_id": "BAD-MOVE",
            "change_type": "MOVE",
            "node_id": "C-SURGE",
            "decision": "CONFIRMED",
            "after": {
                "name": "浪涌防护",
                "parent_id": "MISSING-PARENT",
                "path": ["不存在", "浪涌防护"],
            },
        },
    )
    _add(
        service,
        "JOB-BAD",
        "GOOD-ADD-BEFORE-FAIL",
        "C-WOULD-BE-PARTIAL",
        "不会部分生效",
        ["电源", "临时"],
        parent_id="C-ROOT",
    )
    service.ready_to_apply("JOB-BAD")

    with pytest.raises(HardwareTreeImportContractError, match="TREE_PARENT_NOT_FOUND"):
        service.apply("JOB-BAD")

    failed = service.get_import("JOB-BAD")
    assert failed["status"] == "APPLY_FAILED"
    assert failed["error_code"] == "TREE_PARENT_NOT_FOUND"
    assert service.active_version("CIRCUIT_FEATURE")["version_id"] == "C-001"
    current_ids = {
        node["node_id"]
        for node in service.repository.case_repository.list_tree_nodes("CIRCUIT_FEATURE")
    }
    assert "C-WOULD-BE-PARTIAL" not in current_ids
    assert current_ids == {"C-ROOT", "C-SURGE"}

    # Explicit retry is allowed only after the failed atomic Apply has been
    # surfaced; the next Apply will re-run the same all-or-nothing checks.
    retried = service.ready_to_apply("JOB-BAD")
    assert retried["status"] == "READY_TO_APPLY"


def test_m1_case_mapping_keeps_tree_version_and_path_snapshot_after_tree_move(tmp_path: Path):
    service = _service(tmp_path)
    assert _apply_initial_tree(service) == "C-001"

    case_repo = HardwareCaseRepository(tmp_path / "hardware.sqlite3")
    backend = HardwareCaseBackendService(case_repo)
    backend.create_case(
        {
            "case_id": "HC-SNAPSHOT-1",
            "title": "浪涌问题",
            "case_status": "PENDING_REVIEW",
            "processing_status": "READY",
            "source_refs": ["word:synthetic.docx"],
            "facts": {},
        }
    )
    mapping = backend.set_mapping(
        {
            "mapping_id": "MAP-SNAPSHOT-1",
            "case_id": "HC-SNAPSHOT-1",
            "tree_type": "CIRCUIT_FEATURE",
            "node_id": "C-SURGE",
            "relation_role": "PRIMARY",
            "mapping_status": "CONFIRMED",
            "confidence": 1.0,
            "basis_refs": [],
        }
    )
    assert mapping["tree_version"] == "C-001"
    assert mapping["path_snapshot"] == "电源/输入保护/浪涌防护"

    _job(service, "JOB-C-MOVE")
    _to_review(service, "JOB-C-MOVE")
    service.save_change(
        "JOB-C-MOVE",
        {
            "change_id": "MOVE-SURGE",
            "change_type": "MOVE",
            "node_id": "C-SURGE",
            "decision": "CONFIRMED",
            "after": {
                "name": "浪涌防护",
                "parent_id": "C-ROOT",
                "path": ["电源", "安全保护", "浪涌防护"],
            },
        },
    )
    service.ready_to_apply("JOB-C-MOVE")
    assert service.apply("JOB-C-MOVE")["active_version"]["version_id"] == "C-002"

    preserved = case_repo.get_mapping("MAP-SNAPSHOT-1")
    assert preserved["node_id"] == "C-SURGE"
    assert preserved["tree_version"] == "C-001"
    assert preserved["path_snapshot"] == "电源/输入保护/浪涌防护"
    assert case_repo.get_tree_node("C-SURGE")["path"] == ["电源", "安全保护", "浪涌防护"]


def test_m1_deprecate_hides_node_from_active_tree_without_breaking_history(tmp_path: Path):
    service = _service(tmp_path)
    _apply_initial_tree(service)

    _job(service, "JOB-DEPRECATE")
    _to_review(service, "JOB-DEPRECATE")
    service.save_change(
        "JOB-DEPRECATE",
        {
            "change_id": "DEP-SURGE",
            "change_type": "DEPRECATE",
            "node_id": "C-SURGE",
            "decision": "CONFIRMED",
        },
    )
    service.ready_to_apply("JOB-DEPRECATE")
    service.apply("JOB-DEPRECATE")

    backend = HardwareCaseBackendService(
        HardwareCaseRepository(tmp_path / "hardware.sqlite3")
    )
    active_ids = {node["node_id"] for node in backend.get_tree("CIRCUIT_FEATURE")["nodes"]}
    assert "C-SURGE" not in active_ids
    stored = backend.repository.get_tree_node("C-SURGE")
    assert stored is not None and stored["active"] is False


def test_m1_circuit_and_material_versions_are_independent(tmp_path: Path):
    service = _service(tmp_path)
    assert _apply_initial_tree(service) == "C-001"

    _job(service, "JOB-M-001", tree_type="MATERIAL_DEVICE", filename="material.xlsx")
    _to_review(service, "JOB-M-001")
    _add(
        service,
        "JOB-M-001",
        "M-ROOT-ADD",
        "M-ROOT",
        "连接器",
        ["连接器"],
        tree_type="MATERIAL_DEVICE",
    )
    service.ready_to_apply("JOB-M-001")
    result = service.apply("JOB-M-001")

    assert result["active_version"]["version_id"] == "M-001"
    assert service.active_version("CIRCUIT_FEATURE")["version_id"] == "C-001"
    assert service.active_version("MATERIAL_DEVICE")["version_id"] == "M-001"
