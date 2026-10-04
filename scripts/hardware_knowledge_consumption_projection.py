"""Inspect or rebuild the local Hardware Knowledge consumption projection.

This operational utility writes only the rebuildable projection database. It
never writes Unified Knowledge, the Durable Candidate store, or promotion data.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from services.hardware_asset_repository import CandidateAssetRepository
from services.hardware_case_knowledge_adapter import (
    HardwareCaseKnowledgeAdapter,
    KnowledgeHttpTransport,
)
from services.hardware_knowledge_consumption import (
    PROJECTION_FILENAME,
    HardwareKnowledgeConsumptionError,
    HardwareKnowledgeConsumptionProjectionStore,
    HardwareKnowledgeConsumptionService,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=("status", "rebuild", "project"),
        help="Read projection status, rebuild all eligible records, or project one verified candidate.",
    )
    parser.add_argument(
        "--data-root",
        default=os.getenv("HARDWARE_DATA_ROOT"),
        help="Persistent Data Root (defaults to HARDWARE_DATA_ROOT).",
    )
    parser.add_argument("--asset-candidate-id")
    parser.add_argument(
        "--knowledge-base-url",
        default=os.getenv("HARDWARE_KNOWLEDGE_BASE_URL"),
    )
    parser.add_argument(
        "--knowledge-release-version",
        default=os.getenv("HARDWARE_KNOWLEDGE_RELEASE_VERSION"),
    )
    return parser


def _builder_service(args: argparse.Namespace) -> HardwareKnowledgeConsumptionService:
    data_root = Path(str(args.data_root or "")).expanduser()
    if not args.data_root or not data_root.is_dir():
        raise HardwareKnowledgeConsumptionError("PERSISTENT_DATA_ROOT_REQUIRED")
    asset_db = data_root / "db" / "hardware_asset.db"
    if not asset_db.is_file():
        raise HardwareKnowledgeConsumptionError("CANDIDATE_ASSET_DB_NOT_FOUND")
    if not args.knowledge_base_url or not args.knowledge_release_version:
        raise HardwareKnowledgeConsumptionError("KNOWLEDGE_CONFIG_REQUIRED")
    adapter = HardwareCaseKnowledgeAdapter(
        KnowledgeHttpTransport(args.knowledge_base_url),
        knowledge_release_version=args.knowledge_release_version,
    )
    return HardwareKnowledgeConsumptionService(
        HardwareKnowledgeConsumptionProjectionStore(
            data_root / "rebuildable" / PROJECTION_FILENAME
        ),
        candidate_repository=CandidateAssetRepository(asset_db),
        knowledge_adapter=adapter,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.data_root:
        print("PERSISTENT_DATA_ROOT_REQUIRED", file=sys.stderr)
        return 2
    data_root = Path(args.data_root).expanduser()
    store = HardwareKnowledgeConsumptionProjectionStore(
        data_root / "rebuildable" / PROJECTION_FILENAME
    )
    try:
        if args.operation == "status":
            result: dict[str, Any] = store.projection_status()
        else:
            if args.operation == "project" and not args.asset_candidate_id:
                raise HardwareKnowledgeConsumptionError("CANDIDATE_ID_REQUIRED")
            service = _builder_service(args)
            result = (
                service.rebuild_all_verified()
                if args.operation == "rebuild"
                else service.project_verified(args.asset_candidate_id)
            )
    except HardwareKnowledgeConsumptionError as error:
        print(error.code, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
