"""G2-D P0 Preview-before-Commit tests for real PLC Excel workbooks."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.p0.intake_service import P0IntakeError, P0IntakeService
from quality_knowledge.p0.insight_service import P0InsightService
from quality_knowledge.p0.repository import P0Repository


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_repository(tmp_path) -> P0Repository:
    db_path = tmp_path / "p0-intake.db"
    P0Initializer(
        manifest_path=PROJECT_ROOT / "quality_knowledge" / "config" / "p0_seed_manifest.json",
        plc_seed_path=PROJECT_ROOT / "quality_knowledge" / "config" / "plc_fields.yaml",
    ).initialize(db_path)
    return P0Repository(db_path)


def plc_headers(repository, count=50):
    with repository.connect() as connection:
        rows = connection.execute(
            """SELECT item.target_domain || '.' || item.target_field AS path, alias.alias
                 FROM mapping_item AS item JOIN mapping_alias AS alias ON alias.mapping_item_id = item.mapping_item_id
                WHERE item.config_id = 'MAP-PLC-V1' AND item.enabled = 1
                GROUP BY item.mapping_item_id
                ORDER BY item.display_order"""
        ).fetchall()
        technical = connection.execute(
            """SELECT alias.alias FROM mapping_item AS item JOIN mapping_alias AS alias ON alias.mapping_item_id = item.mapping_item_id
                WHERE item.config_id = 'MAP-PLC-V1' AND item.target_field = 'source_id'"""
        ).fetchone()[0]
    selected = [(row[0], row[1]) for row in rows[:count]]
    required_index = next(index for index, item in enumerate(selected) if item[0] == "ISSUE_FACT.business_issue_id")
    return selected, required_index, technical


def write_workbook(path: Path, headers, values, *, title_row=True, extra_sheet=False):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Data"
    if title_row:
        worksheet.append(["PLC 质量问题导入"])
    worksheet.append(headers)
    rows = values if values and isinstance(values[0], (list, tuple)) else [values]
    for row in rows:
        worksheet.append(row)
    if extra_sheet:
        cover = workbook.create_sheet("Cover")
        cover.append(["封面"])
    workbook.save(path)
    workbook.close()


def break_dimension(path: Path):
    rewritten = path.with_name("broken_dimension.xlsx")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(rewritten, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                content = content.replace(b'<dimension ref="A1:AY3"/>', b'<dimension ref="A1:A1"/>')
            target.writestr(item, content)
    return rewritten


def test_preview_reads_51_column_plc_single_sheet_repairs_dimension_and_keeps_sourceid_raw(tmp_path):
    repository = make_repository(tmp_path)
    fields, required_index, source_id_header = plc_headers(repository)
    headers = [header for _, header in fields] + [source_id_header]
    assert len(headers) == 51
    values = [f"value-{index}" for index in range(50)] + ["feishu-tech-001"]
    values[required_index] = "PLC-BIZ-001"
    workbook = tmp_path / "plc-51.xlsx"
    write_workbook(workbook, headers, values, extra_sheet=True)
    preview = P0IntakeService(repository).preview(break_dimension(workbook))
    assert preview["sheet_name"] == "Data"
    assert preview["header_row"] == 2
    assert preview["total_rows"] == 1
    assert len(preview["field_decisions"]) == 51
    source = next(item for item in preview["field_decisions"] if item["source_header"] == source_id_header)
    assert source["status"] == "RAW_ONLY" and source["target"] is None and source["required"] is False
    assert preview["required_missing"] == []
    candidate = preview["rows"][0]
    assert candidate["raw_json"][source_id_header] == "feishu-tech-001"
    assert "source_id" not in candidate["normalized_candidate"].get("PRODUCT_EXTENSION", {})
    assert len(preview["sheet_candidates"]) == 2


def test_preview_reports_ambiguous_change_impact_and_required_missing_before_confirm(tmp_path):
    repository = make_repository(tmp_path)
    _, _, source_id_header = plc_headers(repository)
    conflict_book = tmp_path / "conflict.xlsx"
    write_workbook(conflict_book, ["ITR单号", "变更影响", source_id_header], ["PLC-BIZ-002", "x", "raw"])
    intake = P0IntakeService(repository)
    conflict = intake.preview(conflict_book)
    decision = next(item for item in conflict["field_decisions"] if item["source_header"] == "变更影响")
    assert decision["status"] == "CONFLICT"
    assert [item["path"] for item in decision["candidates"]] == [
        "ISSUE_FACT.impact", "PRODUCT_EXTENSION.change_impact"
    ]
    assert conflict["has_conflict"] is True
    with pytest.raises(P0IntakeError, match="P0_INTAKE_CONFIRM_BLOCKED"):
        intake.confirm(conflict["preview_token"])

    missing_book = tmp_path / "missing.xlsx"
    write_workbook(missing_book, ["问题描述", source_id_header], ["缺少业务号", "raw"])
    missing = intake.preview(missing_book)
    assert missing["required_missing"] == ["ISSUE_FACT.business_issue_id"]
    assert missing["required_missing_details"] == [{
        "path": "ISSUE_FACT.business_issue_id",
        "label_zh": "问题编号",
        "reason": "SOURCE_HEADER_NOT_FOUND",
        "row_numbers": [3],
    }]
    with pytest.raises(P0IntakeError, match="P0_INTAKE_CONFIRM_BLOCKED"):
        intake.confirm(missing["preview_token"])


def test_confirm_persists_snapshot_commits_once_and_detects_mapping_change(tmp_path):
    repository = make_repository(tmp_path)
    _, _, source_id_header = plc_headers(repository)
    workbook = tmp_path / "confirm.xlsx"
    write_workbook(workbook, ["ITR单号", "问题描述", source_id_header], ["PLC-BIZ-003", "可提交问题", "raw-003"])
    intake = P0IntakeService(repository)
    preview = intake.preview(workbook)
    assert preview["product_id"] == "PRODUCT-PLC"
    assert preview["product_code"] == "PLC"
    result = intake.confirm(preview["preview_token"])
    assert result == {
        "outcome": "APPLIED", "import_batch_id": result["import_batch_id"], "status": "COMPLETED",
        "total": 1, "success": 1, "already_applied": 0, "duplicate": 0,
        "unique_business_issues": 1, "failed": 0,
    }
    issue = repository.get_issue(P0IntakeService._knowledge_id("PLC", "PLC-BIZ-003"))
    assert issue["product_id"] == "PRODUCT-PLC"
    assert issue["raw_json"][source_id_header] == "raw-003"
    assert "source_id" not in issue["normalized_snapshot"].get("PRODUCT_EXTENSION", {})
    assert P0InsightService(repository).overview({"product": "PLC"})["coverage"]["total_issues"] == 1
    repeated = intake.confirm(preview["preview_token"])
    assert repeated["outcome"] == "ALREADY_APPLIED" and repeated["total"] == 1
    with repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_batch").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM quality_issue").fetchone()[0] == 1

    changed_preview = intake.preview(workbook)
    with repository.connect() as connection:
        connection.execute("UPDATE mapping_config SET content_hash = 'changed-after-preview' WHERE config_id = 'MAP-PLC-V1'")
        connection.commit()
    with pytest.raises(P0IntakeError, match="P0_MAPPING_CHANGED_AFTER_PREVIEW"):
        intake.confirm(changed_preview["preview_token"])


def test_preview_requires_an_enabled_product_for_the_business_type(tmp_path):
    repository = make_repository(tmp_path)
    _, _, source_id_header = plc_headers(repository)
    workbook = tmp_path / "product-status.xlsx"
    write_workbook(workbook, ["ITR单号", "问题描述", source_id_header], ["PLC-PRODUCT-001", "产品状态", "raw"])
    intake = P0IntakeService(repository)

    with repository.connect() as connection:
        connection.execute("UPDATE product_config SET enabled = 0 WHERE product_code = 'PLC'")
        connection.commit()
    with pytest.raises(P0IntakeError, match="P0_PRODUCT_DISABLED"):
        intake.preview(workbook)

    with repository.connect() as connection:
        connection.execute("DELETE FROM product_config WHERE product_code = 'PLC'")
        connection.commit()
    with pytest.raises(P0IntakeError, match="P0_PRODUCT_NOT_FOUND"):
        intake.preview(workbook)


def test_preview_keeps_formula_data_when_workbook_has_no_cached_formula_result(tmp_path):
    repository = make_repository(tmp_path)
    _, _, source_id_header = plc_headers(repository)
    workbook = tmp_path / "formula-data.xlsx"
    write_workbook(
        workbook,
        ["ITR单号", "问题描述", source_id_header],
        ["PLC-FORMULA-001", '=CONCAT("formula", " data")', "feishu-tech-formula"],
    )

    preview = P0IntakeService(repository).preview(workbook)

    assert preview["header_row"] == 2
    assert preview["data_start_row"] == 3
    assert preview["total_rows"] == 1
    assert preview["required_missing"] == []
    assert preview["rows"][0]["raw_json"]["问题描述"] == '=CONCAT("formula", " data")'
    assert preview["rows"][0]["normalized_candidate"]["ISSUE_FACT"]["description"] == '=CONCAT("formula", " data")'


def test_confirm_refuses_existing_running_batch_instead_of_claiming_success(tmp_path):
    repository = make_repository(tmp_path)
    _, _, source_id_header = plc_headers(repository)
    workbook = tmp_path / "running-batch.xlsx"
    write_workbook(workbook, ["ITR单号", "问题描述", source_id_header], ["PLC-RUN-001", "运行中", "raw"])
    intake = P0IntakeService(repository)
    preview = intake.preview(workbook)
    with repository.connect() as connection:
        connection.execute(
            "INSERT INTO import_batch(import_batch_id, preview_token, status) VALUES (?, ?, 'RUNNING')",
            ("IMPORT-RUNNING", preview["preview_token"]),
        )
        connection.commit()

    with pytest.raises(P0IntakeError, match="P0_IMPORT_IN_PROGRESS"):
        intake.confirm(preview["preview_token"])
    with repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM quality_issue").fetchone()[0] == 0


def test_confirm_reports_duplicate_and_already_applied_source_rows_truthfully(tmp_path):
    repository = make_repository(tmp_path)
    _, _, source_id_header = plc_headers(repository)
    workbook = tmp_path / "duplicate-business-id.xlsx"
    write_workbook(
        workbook,
        ["ITR单号", "问题描述", source_id_header],
        [
            ["PLC-DUP-001", "首个版本", "tech-1"],
            ["PLC-DUP-001", "首个版本", "tech-2"],
            ["PLC-DUP-001", "后续版本", "tech-3"],
        ],
    )
    intake = P0IntakeService(repository)
    preview = intake.preview(workbook)

    result = intake.confirm(preview["preview_token"])

    assert result["total"] == 3
    assert result["success"] == 2
    assert result["already_applied"] == 1
    assert result["duplicate"] == 2
    assert result["unique_business_issues"] == 1
    assert result["failed"] == 0
    repeated = intake.confirm(preview["preview_token"])
    assert repeated["outcome"] == "ALREADY_APPLIED"
    assert repeated["success"] == 2
    assert repeated["already_applied"] == 1
    assert repeated["duplicate"] == 2
    with repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM quality_issue").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM quality_issue_version").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM issue_source_raw").fetchone()[0] == 2
