from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.hardware_data_reliability import HardwareDataReliabilityManager


def main() -> int:
    parser = argparse.ArgumentParser(description="Hardware DB data reliability control")
    parser.add_argument("--db", required=True)
    parser.add_argument("--backup-root")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status")
    sub.add_parser("migrate")

    backup = sub.add_parser("backup")
    backup.add_argument("--reason", default="MANUAL")
    backup.add_argument("--source-schema", type=int)
    backup.add_argument("--target-schema", type=int)

    restore = sub.add_parser("restore")
    restore.add_argument("backup_id")
    restore.add_argument("--target-db")

    recover = sub.add_parser("recover")
    recover.add_argument("backup_id")

    args = parser.parse_args()
    manager = HardwareDataReliabilityManager(
        Path(args.db),
        backup_root=Path(args.backup_root) if args.backup_root else None,
    )

    if args.command == "status":
        result = manager.inspect_status()
    elif args.command == "migrate":
        result = manager.ensure_ready()
    elif args.command == "backup":
        result = manager.create_backup(
            reason=args.reason,
            source_schema=args.source_schema,
            target_schema=args.target_schema,
        )
    elif args.command == "restore":
        result = manager.restore(
            args.backup_id,
            target_db_path=args.target_db,
        )
    elif args.command == "recover":
        result = manager.recover_from_backup(args.backup_id)
    else:
        raise SystemExit("UNKNOWN_COMMAND")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
