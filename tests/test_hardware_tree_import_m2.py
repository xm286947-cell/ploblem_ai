from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import openpyxl
import pytest

from repositories.hardware_tree_import_repository import HardwareTreeImportRepository
from services.hardware_tree_excel import HardwareTreeImportAnalyzer
from services.hardware_tree_import_contract import HardwareTreeImportContractError


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _xlsx(
    path: Path,
    rows: list[list[object]],
    *,
    sheet: str = "分类",
    header_row: int = 1,
    headers: list[str] | None = None,
):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for _ in range(1, header_row):
        ws.append(["说明"])
    ws.append(headers or ["编码", "L1", "L2", "L3", "L4", "备注"])
    for row in rows:
        ws.append(row)
    wb.create_sheet("其他Sheet")
    wb.save(path)
    wb.close()


def _repo(tmp_path: Path) -> HardwareTreeImportRepository:
    return HardwareTreeImportRepository(tmp_path / "hardware.sqlite3")


def _create_job(repo: HardwareTreeImportRepository, path: Path, job_id: str):
    return repo.create_job(
        job_id=job_id,
        tree_type="CIRCUIT_FEATURE",
        source_filename=path.name,
        source_sha256=_hash(path),
        operator="maintainer-01",
    )


def _profile(depth: int = 4):
    return {
        "sheet_name": "分类",
        "header_row": 3,
        "path_columns": [f"L{i}" for i in range(1, depth + 1)],
        "metadata_columns": ["备注"],
        "business_key_column": "编码",
    }


def _confirm_mutations(repo: HardwareTreeImportRepository, job_id: str):
    for change in repo.list_changes(job_id):
        if change["change_type"] in {"ADD", "UPDATE", "RENAME", "MOVE", "DEPRECATE"}:
            repo.set_change_decision(change["change_id"], "CONFIRMED")


def test_m2_inspect_and_parse_dynamic_depth_non_first_header(tmp_path: Path):
    path = tmp_path / "circuit.xlsx"
    _xlsx(
        path,
        [
            ["K-1", "系统", "模块", "功能", "特性", "A"],
            ["K-2", "系统", "模块", "功能", "子特性", "B"],
        ],
        header_row=3,
    )
    repo = _repo(tmp_path)
    _create_job(repo, path, "JOB-DYNAMIC")
    analyzer = HardwareTreeImportAnalyzer(repo)

    inspected = analyzer.inspect(path)
    assert {item["sheet_name"] for item in inspected["sheets"]} == {"分类", "其他Sheet"}

    result = analyzer.analyze("JOB-DYNAMIC", path, _profile(4))
    assert result["job"]["status"] == "REVIEW_REQUIRED"
    assert result["preview"]["stats"]["max_depth"] == 4
    assert result["preview"]["stats"]["candidate_node_count"] == 5
    assert result["change_summary"]["ADD"] == 5
    assert result["job"]["mapping_profile"]["path_columns"] == ["L1", "L2", "L3", "L4"]


def test_m2_duplicate_is_collapsed_but_parent_gap_is_explicit_issue(tmp_path: Path):
    path = tmp_path / "issues.xlsx"
    duplicate = ["K-1", "系统", "模块", "功能", "特性", "A"]
    _xlsx(
        path,
        [
            duplicate,
            duplicate,
            ["K-BAD", "系统", "", "功能", "特性2", "bad"],
        ],
        header_row=3,
    )
    repo = _repo(tmp_path)
    _create_job(repo, path, "JOB-ISSUES")
    result = HardwareTreeImportAnalyzer(repo).analyze(
        "JOB-ISSUES", path, _profile(4)
    )

    issue_types = {item["issue_type"]: item for item in result["issues"]}
    assert issue_types["EXACT_DUPLICATE"]["resolved"] is True
    assert issue_types["PARENT_LEVEL_MISSING"]["resolved"] is False
    assert result["preview"]["stats"]["duplicate_row_count"] == 1
    assert result["preview"]["stats"]["candidate_node_count"] == 4

    _confirm_mutations(repo, "JOB-ISSUES")
    with pytest.raises(
        HardwareTreeImportContractError, match="UNRESOLVED_VALIDATION_ISSUES"
    ):
        repo.mark_ready_to_apply("JOB-ISSUES")


def test_m2_initial_analysis_can_apply_as_versioned_tree(tmp_path: Path):
    path = tmp_path / "initial.xlsx"
    _xlsx(
        path,
        [["K-USB", "连接器", "特殊连接器", "USB", "", "接口"]],
        header_row=3,
    )
    repo = _repo(tmp_path)
    _create_job(repo, path, "JOB-INITIAL")
    analyzer = HardwareTreeImportAnalyzer(repo)
    result = analyzer.analyze("JOB-INITIAL", path, _profile(4))

    assert result["change_summary"]["ADD"] == 3
    _confirm_mutations(repo, "JOB-INITIAL")
    repo.mark_ready_to_apply("JOB-INITIAL")
    applied = repo.apply_job("JOB-INITIAL")
    assert applied["active_version"]["version_id"] == "C-001"
    assert {
        tuple(node["path"])
        for node in repo.case_repository.list_tree_nodes("CIRCUIT_FEATURE")
    } == {
        ("连接器",),
        ("连接器", "特殊连接器"),
        ("连接器", "特殊连接器", "USB"),
    }


def test_m2_business_key_move_keeps_stable_node_identity(tmp_path: Path):
    initial = tmp_path / "initial.xlsx"
    _xlsx(
        initial,
        [["K-USB", "连接器", "特殊连接器", "USB", "", "旧"]],
        header_row=3,
    )
    repo = _repo(tmp_path)
    _create_job(repo, initial, "JOB-I")
    analyzer = HardwareTreeImportAnalyzer(repo)
    analyzer.analyze("JOB-I", initial, _profile(4))
    _confirm_mutations(repo, "JOB-I")
    repo.mark_ready_to_apply("JOB-I")
    repo.apply_job("JOB-I")

    old_usb = next(
        node
        for node in repo.case_repository.list_tree_nodes("CIRCUIT_FEATURE")
        if node["business_key"] == "K-USB"
    )
    old_id = old_usb["node_id"]

    updated = tmp_path / "updated.xlsx"
    _xlsx(
        updated,
        [["K-USB", "连接器", "高速接口", "USB", "", "新"]],
        header_row=3,
    )
    _create_job(repo, updated, "JOB-U")
    result = analyzer.analyze("JOB-U", updated, _profile(4))

    by_type = {}
    for item in result["changes"]:
        by_type.setdefault(item["change_type"], []).append(item)
    move = by_type["MOVE"][0]
    assert move["node_id"] == old_id
    assert move["after"]["node_id"] == old_id
    assert move["after"]["path"] == ["连接器", "高速接口", "USB"]
    assert result["change_summary"]["ADD"] == 1
    # Old "特殊连接器" is omitted from the Excel but is not auto-deleted.
    assert all(item["change_type"] != "DEPRECATE" for item in result["changes"])

    _confirm_mutations(repo, "JOB-U")
    repo.mark_ready_to_apply("JOB-U")
    repo.apply_job("JOB-U")

    new_usb = repo.case_repository.get_tree_node(old_id)
    assert new_usb["path"] == ["连接器", "高速接口", "USB"]
    assert repo.case_repository.get_tree_node(
        next(
            node["node_id"]
            for node in repo.case_repository.list_tree_nodes("CIRCUIT_FEATURE")
            if node["name"] == "特殊连接器"
        )
    )["active"] is True


def test_m2_path_change_without_business_key_is_conflict_not_silent_move(tmp_path: Path):
    initial = tmp_path / "no-key-initial.xlsx"
    _xlsx(
        initial,
        [["", "连接器", "特殊连接器", "USB", "", ""]],
        header_row=3,
    )
    repo = _repo(tmp_path)
    _create_job(repo, initial, "JOB-NK-I")
    analyzer = HardwareTreeImportAnalyzer(repo)
    analyzer.analyze("JOB-NK-I", initial, _profile(4))
    _confirm_mutations(repo, "JOB-NK-I")
    repo.mark_ready_to_apply("JOB-NK-I")
    repo.apply_job("JOB-NK-I")

    updated = tmp_path / "no-key-updated.xlsx"
    _xlsx(
        updated,
        [["", "连接器", "高速接口", "USB", "", ""]],
        header_row=3,
    )
    _create_job(repo, updated, "JOB-NK-U")
    result = analyzer.analyze("JOB-NK-U", updated, _profile(4))

    conflicts = [
        item for item in result["changes"] if item["change_type"] == "CONFLICT"
    ]
    assert len(conflicts) == 1
    assert conflicts[0]["issue_code"] == "PATH_CHANGED_WITHOUT_BUSINESS_KEY"
    assert result["change_summary"]["CONFLICT"] == 1
    # Diff conflicts belong to Step 4 and must not also remain as Step 3
    # validation blockers after the user resolves/excludes the Change.
    assert all(
        issue["issue_type"] != "PATH_CHANGED_WITHOUT_BUSINESS_KEY"
        for issue in result["issues"]
    )
    for change in result["changes"]:
        if change["change_type"] in {"ADD", "UPDATE", "RENAME", "MOVE", "DEPRECATE"}:
            repo.set_change_decision(change["change_id"], "CONFIRMED")
    repo.set_change_decision(
        conflicts[0]["change_id"],
        "RESOLVED",
        resolved_after=conflicts[0]["after"],
    )
    assert repo.mark_ready_to_apply("JOB-NK-U")["status"] == "READY_TO_APPLY"


def test_m2_same_business_key_multiple_paths_is_never_auto_applied(tmp_path: Path):
    path = tmp_path / "key-conflict.xlsx"
    _xlsx(
        path,
        [
            ["K-X", "连接器", "A", "USB", "", ""],
            ["K-X", "连接器", "B", "USB", "", ""],
        ],
        header_row=3,
    )
    repo = _repo(tmp_path)
    _create_job(repo, path, "JOB-K-CONFLICT")
    result = HardwareTreeImportAnalyzer(repo).analyze(
        "JOB-K-CONFLICT", path, _profile(4)
    )
    assert any(
        issue["issue_type"] == "BUSINESS_KEY_MULTIPLE_PATHS"
        for issue in result["issues"]
    )
    assert result["change_summary"]["CONFLICT"] >= 1


def test_m2_source_filename_and_hash_are_audit_boundaries(tmp_path: Path):
    path = tmp_path / "source.xlsx"
    _xlsx(
        path,
        [["K-1", "系统", "模块", "功能", "", ""]],
        header_row=3,
    )
    repo = _repo(tmp_path)
    repo.create_job(
        job_id="JOB-HASH",
        tree_type="CIRCUIT_FEATURE",
        source_filename=path.name,
        source_sha256="0" * 64,
        operator="maintainer-01",
    )

    with pytest.raises(HardwareTreeImportContractError, match="SOURCE_HASH_MISMATCH"):
        HardwareTreeImportAnalyzer(repo).analyze("JOB-HASH", path, _profile(4))
    assert repo.get_job("JOB-HASH")["status"] == "UPLOADED"


def test_m2_missing_mapping_column_fails_closed_without_guessing(tmp_path: Path):
    path = tmp_path / "bad-mapping.xlsx"
    _xlsx(
        path,
        [["K-1", "系统", "模块", "功能", "", ""]],
        header_row=3,
    )
    repo = _repo(tmp_path)
    _create_job(repo, path, "JOB-BAD-MAP")
    profile = _profile(4)
    profile["path_columns"] = ["L1", "L2", "不存在的列"]

    with pytest.raises(
        HardwareTreeImportContractError, match="MAPPING_COLUMN_NOT_FOUND"
    ):
        HardwareTreeImportAnalyzer(repo).analyze(
            "JOB-BAD-MAP", path, profile
        )
    assert repo.get_job("JOB-BAD-MAP")["status"] == "PARSE_FAILED"
