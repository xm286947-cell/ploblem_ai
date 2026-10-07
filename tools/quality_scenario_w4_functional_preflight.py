#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


CONTRACT = "quality-scenario-w4-functional-fixture/v1"
TYPE_BY_KEY = {
    "software_assessment_material_id": "SOFTWARE_OPERATION",
    "itr_material_id": "ITR_SOURCE",
    "resolution_material_id": "ITR_CS",
    "missed_test_material_id": "ESCAPE_ANALYSIS",
    "conflict_resolution_material_id": "ITR_CS",
}


def _row_types(db: Path) -> dict[str, str]:
    with sqlite3.connect(db) as connection:
        return {
            str(row[0]): str(row[1])
            for row in connection.execute(
                "SELECT material_id,material_type FROM source_material"
            )
        }


def _qsv1_count(db: Path) -> int:
    with sqlite3.connect(db) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='quality_scenario_v1'"
        ).fetchone()
        if not exists:
            return 0
        return int(connection.execute("SELECT COUNT(*) FROM quality_scenario_v1").fetchone()[0] or 0)


def inspect(db: Path) -> tuple[dict[str, Any], list[str]]:
    manifest_path = db.with_suffix(db.suffix + ".fixture.json")
    errors: list[str] = []
    if not db.is_file():
        return {}, ["W4_FIXTURE_DB_MISSING"]
    if not manifest_path.is_file():
        return {}, ["W4_FIXTURE_MANIFEST_MISSING"]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("contract") != CONTRACT:
        errors.append("W4_FIXTURE_CONTRACT_MISMATCH")
    if manifest.get("synthetic_business_data") is not True:
        errors.append("W4_FIXTURE_SYNTHETIC_FLAG_REQUIRED")
    if manifest.get("direct_qsv1_candidate_write") is not False:
        errors.append("W4_FIXTURE_DIRECT_CANDIDATE_WRITE_FORBIDDEN")
    if manifest.get("direct_qsv1_publish_write") is not False:
        errors.append("W4_FIXTURE_DIRECT_PUBLISH_WRITE_FORBIDDEN")

    types = _row_types(db)
    cases = manifest.get("cases") or []
    if len(cases) != 5:
        errors.append(f"W4_FIXTURE_CASE_COUNT_INVALID:{len(cases)}")

    by_case = {str(item.get("case_id") or ""): item for item in cases if isinstance(item, dict)}
    required_cases = {
        "G1_COMPLETE",
        "G2_NO_MISSED_TEST",
        "G3_CONFLICT",
        "G4_DUPLICATE_GENERATE",
        "G5_SOURCE_REVISION",
    }
    missing_cases = sorted(required_cases - set(by_case))
    if missing_cases:
        errors.append("W4_FIXTURE_CASES_MISSING:" + ",".join(missing_cases))

    for case_id, item in sorted(by_case.items()):
        expected = item.get("expected_source_status") or {}
        for key, material_type in TYPE_BY_KEY.items():
            material_id = str(item.get(key) or "").strip()
            should_exist = True
            if key == "missed_test_material_id":
                should_exist = expected.get("MISSED_TEST") == "PRESENT"
            if key == "conflict_resolution_material_id":
                should_exist = expected.get("RESOLUTION") == "CONFLICT"
            if should_exist and not material_id:
                errors.append(f"{case_id}:{key}:MISSING")
                continue
            if material_id and types.get(material_id) != material_type:
                errors.append(
                    f"{case_id}:{key}:TYPE_MISMATCH:{types.get(material_id)}"
                )

        if expected.get("ITR") == "PRESENT" and not item.get("itr_material_id"):
            errors.append(f"{case_id}:ITR_SOURCE_MISSING")

    qsv1_count = _qsv1_count(db)
    if qsv1_count:
        errors.append(f"W4_FIXTURE_PRESEEDED_QSV1_FORBIDDEN:{qsv1_count}")

    result = {
        "contract": manifest.get("contract"),
        "case_count": len(cases),
        "qsv1_count": qsv1_count,
        "cases": {
            case_id: {
                "expected_source_status": item.get("expected_source_status") or {},
                "software_assessment_material_id": item.get("software_assessment_material_id") or "",
                "itr_material_id": item.get("itr_material_id") or "",
                "resolution_material_id": item.get("resolution_material_id") or "",
                "missed_test_material_id": item.get("missed_test_material_id") or "",
            }
            for case_id, item in sorted(by_case.items())
        },
    }
    return result, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--require", action="store_true")
    args = parser.parse_args()

    db = Path(args.db).expanduser().resolve()
    result, errors = inspect(db)
    print(f"W4_FUNCTIONAL_DB={db}")
    print(f"W4_FUNCTIONAL_CASE_COUNT={result.get('case_count', 0)}")
    print(f"W4_FUNCTIONAL_PRESEEDED_QSV1_COUNT={result.get('qsv1_count', 0)}")
    for case_id, item in (result.get("cases") or {}).items():
        status = item.get("expected_source_status") or {}
        print(
            f"{case_id}_EXPECTED="
            f"ASSESSMENT:{status.get('SOFTWARE_ASSESSMENT','')},"
            f"RESOLUTION:{status.get('RESOLUTION','')},"
            f"ITR:{status.get('ITR','')},"
            f"MISSED_TEST:{status.get('MISSED_TEST','')}"
        )
    if errors:
        print("W4_FUNCTIONAL_PREFLIGHT=FAIL")
        for error in errors:
            print("W4_FUNCTIONAL_PREFLIGHT_ERROR=" + error)
        return 3 if args.require else 0
    print("W4_FUNCTIONAL_PREFLIGHT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
