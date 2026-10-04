"""Operator CLI for local Durable Asset backup and offline restore."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from services.hardware_asset_backup import (
    HardwareAssetBackupCoordinator,
    HardwareAssetBackupError,
)
from services.hardware_data_root import HardwareDataRootResolver


def _coordinator() -> HardwareAssetBackupCoordinator:
    application_root = Path(__file__).resolve().parents[1]
    resolver = HardwareDataRootResolver(application_root)
    return HardwareAssetBackupCoordinator(resolver)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hardware Knowledge Durable Backup Set operations")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create", help="Create and publish a verified backup set")
    create.add_argument(
        "--reason",
        default="MANUAL",
        choices=("MANUAL", "PRE_UPGRADE", "PRE_MIGRATION", "PRE_RESTORE_ROLLBACK", "RECOVERY_CHECKPOINT"),
    )
    subparsers.add_parser("list", help="List published backup sets")
    verify = subparsers.add_parser("verify", help="Verify a published backup set")
    verify.add_argument("backup_id")
    restore = subparsers.add_parser("restore", help="Offline restore from a published backup set")
    restore.add_argument("backup_id")
    args = parser.parse_args(argv)

    try:
        coordinator = _coordinator()
        if args.command == "create":
            manifest = coordinator.create_backup(reason=args.reason)
            print(f"BACKUP_ID={manifest['backup_id']}")
            print(f"BACKUP_STATE={manifest['backup_state']}")
            print("RESULT=PASS")
        elif args.command == "list":
            for item in coordinator.list_backups():
                print(f"{item['backup_id']}\t{item['backup_state']}\t{item['reason']}\t{item['created_at']}")
        elif args.command == "verify":
            manifest = coordinator.verify_backup(args.backup_id)
            print(f"BACKUP_ID={manifest['backup_id']}")
            print("VERIFY=PASS")
        elif args.command == "restore":
            result = coordinator.restore(args.backup_id)
            print(f"RESTORE_ID={result['restore_id']}")
            print(f"BACKUP_ID={result['backup_id']}")
            print(f"ROLLBACK_BACKUP_ID={result['rollback_backup_id']}")
            print(f"STARTUP_STATUS={result['startup_status']}")
            print("RESULT=PASS")
        return 0
    except HardwareAssetBackupError as error:
        print(f"RESULT=FAIL")
        print(f"ERROR={error.code}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
