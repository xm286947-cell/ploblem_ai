"""Safe preparation runner for the Wave4 real-document validation package.

The runner freezes metadata only.  It never calls a provider and never writes
to the product data root.  Real documents remain outside the package and are
represented by hashes and structural counters in the dataset manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree


class Wave4ValidationError(RuntimeError):
    pass


def _safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return value[:80] or "case"


def load_config(path: Path) -> dict:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Wave4ValidationError("CONFIG_INVALID") from exc
    if not isinstance(config, dict):
        raise Wave4ValidationError("CONFIG_OBJECT_REQUIRED")
    return config


def _resolve(config_path: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return (config_path.parent / path).resolve() if not path.is_absolute() else path.resolve()


def discover_docx(source_dir: Path, max_cases: int) -> list[Path]:
    if not source_dir.is_dir():
        raise Wave4ValidationError("SOURCE_DIR_INVALID")
    if not 1 <= max_cases <= 30:
        raise Wave4ValidationError("MAX_CASES_INVALID")
    return sorted(p for p in source_dir.rglob("*.docx") if p.is_file())[:max_cases]


def _xml_count(archive: zipfile.ZipFile, name: str, tag: str) -> int:
    try:
        root = ElementTree.fromstring(archive.read(name))
    except (KeyError, ElementTree.ParseError):
        return 0
    return sum(1 for item in root.iter() if item.tag.rsplit("}", 1)[-1] == tag)


def describe_docx(path: Path, source_root: Path) -> dict:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        paragraphs = _xml_count(archive, "word/document.xml", "p")
        tables = _xml_count(archive, "word/document.xml", "tbl")
        images = sum(1 for name in names if name.startswith("word/media/"))
        headings = 0
        try:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
            for paragraph in root.iter():
                if paragraph.tag.rsplit("}", 1)[-1] != "p":
                    continue
                styles = [child for child in paragraph.iter() if child.tag.rsplit("}", 1)[-1] == "pStyle"]
                if any(str(item.attrib.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val", "")).lower().startswith("heading") for item in styles):
                    headings += 1
        except (KeyError, ElementTree.ParseError):
            pass
    relative = path.relative_to(source_root).as_posix()
    return {
        "source_id": digest,
        "file_size": path.stat().st_size,
        "business_case_id": _safe_name(path.stem),
        "heading_count": headings,
        "paragraph_count": paragraphs,
        "table_count": tables,
        "image_block_count": images,
        "text_eligible": bool(paragraphs),
        "image_dependency_classification": "IMAGE_DEPENDENT_DEFERRED" if images else "TEXT_ONLY",
        "relative_name": relative,
    }


def prepare(config_path: Path, output_dir: Path) -> dict:
    config = load_config(config_path)
    validation_root = _resolve(config_path, str(config.get("validation_data_root", "")))
    product_root = _resolve(config_path, str(config.get("normal_product_data_root", "")))
    if validation_root == product_root:
        raise Wave4ValidationError("VALIDATION_DATA_ROOT_COLLISION")
    source_dir = _resolve(config_path, str(config.get("source_dir", "")))
    cases = discover_docx(source_dir, int(config.get("max_cases", 30)))
    records = [describe_docx(path, source_dir) for path in cases]
    ids = [item["source_id"] for item in records]
    if len(ids) != len(set(ids)):
        raise Wave4ValidationError("DUPLICATE_SOURCE_ID")
    if len(records) > 30:
        raise Wave4ValidationError("DATASET_LIMIT_EXCEEDED")
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "manifest_version": "hardware-r1-wave4-dataset/v1",
        "case_count": len(records),
        "source_dir_recorded": False,
        "records": records,
        "provider_calls": 0,
    }
    report = {
        "mode": "prepare",
        "dataset_manifest": manifest,
        "preflight": {
            "data_root_isolation": True,
            "source_dir_exists": True,
            "provider_calls": 0,
            "real_execution": False,
        },
    }
    (output_dir / "DATASET_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "WAVE4_PREPARE_REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a metadata-only Wave4 validation dataset")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    if args.run:
        print("REAL_PROVIDER_RUN_DISABLED", file=sys.stderr)
        return 2
    if not args.prepare:
        print("MODE_REQUIRED=--prepare", file=sys.stderr)
        return 2
    try:
        report = prepare(args.config.resolve(), args.output.resolve())
    except Wave4ValidationError as exc:
        print(f"ERROR_CODE={exc}", file=sys.stderr)
        return 1
    print(f"WAVE4_PREPARE_SMOKE=PASS CASE_COUNT={report['dataset_manifest']['case_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
