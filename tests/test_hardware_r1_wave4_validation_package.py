from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from tools.hardware_r1_wave4_validation import Wave4ValidationError, prepare


def _minimal_docx(path: Path) -> None:
    import zipfile

    document = b'''<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>synthetic</w:t></w:r></w:p></w:body></w:document>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)


def test_prepare_freezes_sanitized_manifest_without_provider(tmp_path: Path):
    source = tmp_path / "external"
    source.mkdir()
    _minimal_docx(source / "case-01.docx")
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "source_dir": "external",
        "max_cases": 30,
        "validation_data_root": "validation",
        "normal_product_data_root": "product",
    }), encoding="utf-8")
    report = prepare(config_path, tmp_path / "report")
    assert report["preflight"]["provider_calls"] == 0
    manifest = json.loads((tmp_path / "report/DATASET_MANIFEST.json").read_text())
    assert manifest["case_count"] == 1
    assert "synthetic" not in json.dumps(manifest)
    assert str(tmp_path) not in json.dumps(manifest)


def test_prepare_fails_closed_on_data_root_collision(tmp_path: Path):
    source = tmp_path / "external"
    source.mkdir()
    _minimal_docx(source / "case.docx")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "source_dir": "external",
        "validation_data_root": "same",
        "normal_product_data_root": "same",
    }), encoding="utf-8")
    with pytest.raises(Wave4ValidationError, match="VALIDATION_DATA_ROOT_COLLISION"):
        prepare(config, tmp_path / "report")
