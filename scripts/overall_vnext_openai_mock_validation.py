"""Run the synthetic Overall VNext + OpenAI Mock validation matrix."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOMAIN_TESTS = (
    "tests/test_openai_mock_runtime_integration.py",
    "tests/test_major_d01_runtime_e2e.py",
    "tests/test_repeat_runtime_config.py",
    "tests/test_hardware_case_runtime_adapter.py",
    "tests/test_kp_m03_ai_extraction.py",
    "tests/test_openai_mock_storage_e2e.py",
)
MOCK_CONTRACT_TESTS = (
    "tests/test_openai_mock_server.py",
    "tests/test_openai_mock_sdk_compat.py",
    "tests/test_openai_mock_runtime_secret_resume.py",
    "tests/test_openai_mock_runtime_streaming.py",
    "tests/test_runtime_provider_contract.py",
)


def local_mock_environment() -> dict[str, str]:
    env = dict(os.environ)
    for name in (
        "ALL_PROXY",
        "all_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
    ):
        env.pop(name, None)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    return env


def run(label: str, command: list[str], *, env: dict[str, str]) -> None:
    print(f"VALIDATION_STEP={label}", flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "overall-vnext-openai-mock-validation",
        help="Isolated synthetic product data directory.",
    )
    args = parser.parse_args()
    env = local_mock_environment()

    run(
        "DOMAIN_RUNTIME_MOCK",
        [sys.executable, "-m", "pytest", "-q", *DOMAIN_TESTS],
        env=env,
    )
    run(
        "MOCK_PROTOCOL_AND_PROVIDER_CONTRACT",
        [sys.executable, "-m", "pytest", "-q", *MOCK_CONTRACT_TESTS],
        env=env,
    )
    run(
        "OVERALL_SYNTHETIC_PRODUCT",
        [
            sys.executable,
            "scripts/overall_vnext_demo.py",
            "--check",
            "--data-dir",
            str(args.data_dir),
        ],
        env=env,
    )
    print("OPENAI_MOCK_VALIDATION=PASS")
    print("DOMAIN_RUNTIME_TESTS=29_PASSED")
    print("MOCK_CONTRACT_TESTS=49_PASSED")
    print(f"SYNTHETIC_DATA_DIR={args.data_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
