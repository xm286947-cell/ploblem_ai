from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "test_assets" / "storage_rc1" / "scenario_catalog.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Storage RC1 product-test gate runner")
    parser.add_argument(
        "--mock-only",
        action="store_true",
        help="Run deterministic Mock/fixture gate only; this is not Product Test Gate.",
    )
    args = parser.parse_args()

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))

    mock_tests = [
        "tests/test_storage_rc1_mock_assets.py",
        "tests/test_storage_rc1_scenario_catalog.py",
        "tests/test_openai_mock_storage_m01_m08.py",
    ]
    if args.mock_only:
        return pytest.main(["-q", *mock_tests])

    missing = [
        path
        for path in catalog["required_sut_package_tests"]
        if not (ROOT / path).exists()
    ]
    if missing:
        print("BLOCKED: Storage RC1 SUT package test assets are missing:")
        for path in missing:
            print(f"  - {path}")
        print("Full Product Test Gate must not be treated as PASS.")
        return 2

    tests = [
        *mock_tests,
        *catalog["existing_repo_tests"],
        *catalog["required_sut_package_tests"],
    ]
    tests = list(dict.fromkeys(tests))
    return pytest.main(["-q", *tests])


if __name__ == "__main__":
    raise SystemExit(main())
