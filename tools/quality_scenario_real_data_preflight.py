#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def count(connection: sqlite3.Connection, sql: str, params=()) -> int:
    try:
        row = connection.execute(sql, params).fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0] or 0) if row else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed preflight for Quality Scenario real-data Browser Golden."
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--require", action="store_true")
    args = parser.parse_args()

    path = Path(args.db).expanduser().resolve()
    print(f"REAL_DATA_DB={path}")
    if not path.is_file():
        print("QUALITY_ISSUE_COUNT=0")
        print("SOFTWARE_OPERATION_SOURCE_FACT_COUNT=0")
        print("ITR_CS_SOURCE_FACT_COUNT=0")
        print("ESCAPE_ANALYSIS_SOURCE_FACT_COUNT=0")
        print("LINKED_SOFTWARE_OPERATION_COUNT=0")
        print("REAL_DATA_PREFLIGHT=NOT_READY")
        return 3 if args.require else 0

    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        print(f"REAL_DATA_PREFLIGHT=DB_OPEN_FAILED:{type(exc).__name__}")
        return 3 if args.require else 0

    with connection:
        issue_count = count(connection, "SELECT COUNT(*) FROM quality_issue")
        sw_ops = count(
            connection,
            "SELECT COUNT(*) FROM source_material WHERE material_type='SOFTWARE_OPERATION'",
        )
        itr_cs = count(
            connection,
            "SELECT COUNT(*) FROM source_material WHERE material_type='ITR_CS'",
        )
        escape = count(
            connection,
            "SELECT COUNT(*) FROM source_material WHERE material_type='ESCAPE_ANALYSIS'",
        )
        linked_sw_ops = count(
            connection,
            """SELECT COUNT(DISTINCT m.material_id)
               FROM source_material m
               JOIN issue_material_link l ON l.material_id=m.material_id
               WHERE m.material_type='SOFTWARE_OPERATION'
                 AND l.link_status IN ('LINKED','MANUAL_LINKED')""",
        )

    print(f"QUALITY_ISSUE_COUNT={issue_count}")
    print(f"SOFTWARE_OPERATION_SOURCE_FACT_COUNT={sw_ops}")
    print(f"ITR_CS_SOURCE_FACT_COUNT={itr_cs}")
    print(f"ESCAPE_ANALYSIS_SOURCE_FACT_COUNT={escape}")
    print(f"LINKED_SOFTWARE_OPERATION_COUNT={linked_sw_ops}")

    ready = issue_count > 0 and sw_ops > 0 and linked_sw_ops > 0
    print("REAL_DATA_PREFLIGHT=" + ("PASS" if ready else "NOT_READY"))
    if args.require and not ready:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
