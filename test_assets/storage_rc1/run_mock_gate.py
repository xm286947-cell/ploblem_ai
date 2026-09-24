from __future__ import annotations

import sys

import pytest


def main() -> int:
    return pytest.main(
        [
            "-q",
            "tests/test_storage_rc1_mock_assets.py",
            "tests/test_openai_mock_storage_m01_m08.py",
        ]
    )


if __name__ == "__main__":
    sys.exit(main())
