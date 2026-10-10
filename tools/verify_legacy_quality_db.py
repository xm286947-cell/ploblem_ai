from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def scalar(connection: sqlite3.Connection, sql: str) -> int:
    row = connection.execute(sql).fetchone()
    return int(row[0] if row else 0)


def main() -> int:
    if len(sys.argv) != 2:
        print("USAGE=python tools/verify_legacy_quality_db.py <quality_issue_v1.db>")
        return 2

    db = Path(sys.argv[1]).expanduser()
    try:
        db = db.resolve(strict=True)
    except FileNotFoundError:
        print(f"MATURE_DB_BOUND=FAIL")
        print(f"REASON=DB_NOT_FOUND")
        print(f"DB_PATH={db}")
        return 3

    if not db.is_file():
        print("MATURE_DB_BOUND=FAIL")
        print("REASON=DB_NOT_FILE")
        print(f"DB_PATH={db}")
        return 3

    uri = f"file:{db.as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

        if "quality_scenario" not in tables:
            print("MATURE_DB_BOUND=FAIL")
            print("REASON=QUALITY_SCENARIO_TABLE_MISSING")
            print(f"DB_PATH={db}")
            return 4

        scenario_count = scalar(connection, "SELECT COUNT(*) FROM quality_scenario")
        evidence_count = (
            scalar(connection, "SELECT COUNT(*) FROM quality_scenario_evidence")
            if "quality_scenario_evidence" in tables
            else 0
        )

        source_tables = {
            "scenario_generation_source",
            "quality_issue",
            "issue_material_link",
            "source_material",
        }
        source_rows = 0
        source_present = []
        for table in sorted(source_tables & tables):
            count = scalar(connection, f'SELECT COUNT(*) FROM "{table}"')
            if count:
                source_present.append(f"{table}:{count}")
                source_rows += count

        print(f"DB_PATH={db}")
        print(f"QUALITY_SCENARIO_COUNT={scenario_count}")
        print(f"QUALITY_SCENARIO_EVIDENCE_COUNT={evidence_count}")
        print(f"PORTRAIT_SOURCE_ROWS={source_rows}")
        print("PORTRAIT_SOURCE_TABLES=" + (",".join(source_present) or "NONE"))

        if scenario_count <= 0:
            print("LEGACY_DATA_VISIBLE=FAIL")
            print("REASON=QUALITY_SCENARIO_EMPTY")
            return 5
        if evidence_count <= 0:
            print("LEGACY_DATA_VISIBLE=FAIL")
            print("REASON=SCENARIO_EVIDENCE_EMPTY")
            return 6
        if source_rows <= 0:
            print("LEGACY_DATA_VISIBLE=FAIL")
            print("REASON=PORTRAIT_SOURCE_EMPTY")
            return 7

        print("MATURE_DB_BOUND=PASS")
        print("LEGACY_DATA_VISIBLE=PASS")
        return 0
    except sqlite3.DatabaseError as exc:
        print("MATURE_DB_BOUND=FAIL")
        print(f"REASON=SQLITE_ERROR:{exc}")
        return 8
    finally:
        if "connection" in locals():
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
