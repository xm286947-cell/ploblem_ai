from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

import openpyxl

from tools.hardware_case_real_validation import load_config, run_validation


DOC_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
 <w:body>
  <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>问题描述</w:t></w:r></w:p>
  <w:p><w:r><w:t>控制器上电复位。</w:t></w:r></w:p>
  <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>原因分析</w:t></w:r></w:p>
  <w:p><w:r><w:t>输入浪涌触发保护。</w:t></w:r></w:p>
  <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>解决方案</w:t></w:r></w:p>
  <w:p><w:r><w:t>增加输入保护器件。</w:t></w:r></w:p>
 </w:body>
</w:document>
"""


def _docx(folder: Path, name: str = "A9001-Synthetic Case.docx") -> Path:
    target = folder / name
    with ZipFile(target, "w") as archive:
        archive.writestr("word/document.xml", DOC_XML)
    return target


def _xlsx(path: Path, rows):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Tree"
    sheet.append(["L1", "L2", "L3", "L4"])
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def _structurer(document):
    by_text = {
        block.get("text"): block["block_id"]
        for block in document["blocks"]
        if block.get("text")
    }
    return {
        "title": "Synthetic Case",
        "facts": {
            "symptom": {
                "value": "控制器上电复位。",
                "evidence_block_ids": [by_text["控制器上电复位。"]],
            },
            "root_cause": {
                "value": "输入浪涌触发保护。",
                "evidence_block_ids": [by_text["输入浪涌触发保护。"]],
            },
            "actions": {
                "value": "增加输入保护器件。",
                "evidence_block_ids": [by_text["增加输入保护器件。"]],
            },
        },
        "circuit_feature_links": [],
        "material_links": [],
    }


def test_real_validation_report_contains_aggregates_only(tmp_path: Path):
    source = tmp_path / "word"
    source.mkdir()
    _docx(source)

    circuit = tmp_path / "circuit.xlsx"
    material = tmp_path / "material.xlsx"
    _xlsx(circuit, [["电源", "输入保护", None, None]])
    _xlsx(material, [["采购件", "保护器件", None, None]])

    config = {
        "_config_dir": str(tmp_path),
        "source_dir": str(source),
        "max_cases": 30,
        "output_db": str(tmp_path / "out" / "validation.sqlite3"),
        "output_report": str(tmp_path / "out" / "metrics.json"),
        "trees": [
            {
                "tree_type": "CIRCUIT_FEATURE",
                "path": str(circuit),
                "sheet": "Tree",
                "path_columns": ["A", "B", "C"],
                "start_row": 2,
            },
            {
                "tree_type": "MATERIAL_DEVICE",
                "path": str(material),
                "sheet": "Tree",
                "path_columns": ["A", "B", "C", "D"],
                "start_row": 2,
            },
        ],
    }

    report = run_validation(config, structurer=_structurer)
    assert report["input_case_count"] == 1
    assert report["parse_success_rate"] == 1.0
    assert report["core_field_extraction_coverage"] == 1.0
    assert report["core_evidence_grounding_rate"] == 1.0
    assert report["tree_node_count"] == {
        "CIRCUIT_FEATURE": 1,
        "MATERIAL_DEVICE": 1,
    }

    raw_report = (tmp_path / "out" / "metrics.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in raw_report
    assert "A9001" not in raw_report
    assert "输入浪涌触发保护" not in raw_report
    assert report["security"] == {
        "contains_source_paths": False,
        "contains_case_text": False,
        "contains_case_ids": False,
        "contains_secrets": False,
    }


def test_real_validation_counts_failures_without_leaking_content(tmp_path: Path):
    source = tmp_path / "word"
    source.mkdir()
    _docx(source)

    tree = tmp_path / "tree.xlsx"
    _xlsx(tree, [["电源", "保护", None, None]])

    def fail(_document):
        raise RuntimeError("sensitive source text must not leak")

    config = {
        "_config_dir": str(tmp_path),
        "source_dir": str(source),
        "output_db": str(tmp_path / "validation.sqlite3"),
        "output_report": str(tmp_path / "metrics.json"),
        "trees": [
            {
                "tree_type": "CIRCUIT_FEATURE",
                "path": str(tree),
                "sheet": "Tree",
                "path_columns": ["A", "B"],
            }
        ],
    }
    report = run_validation(config, structurer=fail)
    assert report["status_counts"]["FAILED"] == 1
    raw = json.dumps(report, ensure_ascii=False)
    assert "sensitive source text" not in raw
    assert "A9001" not in raw


def test_config_resolves_relative_paths_only_at_runtime(tmp_path: Path):
    config_path = tmp_path / "validation.json"
    config_path.write_text(
        json.dumps(
            {
                "source_dir": "./word",
                "output_db": "./out/db.sqlite3",
                "output_report": "./out/metrics.json",
                "structurer": "company_runtime_adapter:structure_hardware_case",
                "trees": [],
            }
        ),
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config["_config_dir"] == str(tmp_path.resolve())
    assert config["source_dir"] == "./word"
