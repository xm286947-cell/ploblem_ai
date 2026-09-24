from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "dist"
PACKAGE = "STORAGE_PRODUCT_TEST_BASELINE_RC1_V0.3.zip"

INCLUDE = [
    "test_assets/storage_rc1",
    "tests/test_storage_rc1_mock_assets.py",
    "tests/test_storage_rc1_scenario_catalog.py",
    "tests/test_openai_mock_storage_m01_m08.py",
    "tests/test_runtime_provider_adapter.py",
    "tests/test_kp_d03_review_publish.py",
    "tests/test_kp_d06_storage_golden.py",
    "tools/openai_mock",
    "requirements-runtime-p0-test.txt",
    "requirements-openai-mock-test.txt",
]

EXCLUDE_NAMES = {"__pycache__", ".pytest_cache", ".DS_Store"}
FIXED_TIME = (2026, 9, 24, 0, 0, 0)


def iter_files() -> list[Path]:
    files: list[Path] = []
    for rel in INCLUDE:
        path = ROOT / rel
        if not path.exists():
            raise SystemExit(f"required package input missing: {rel}")
        if path.is_file():
            files.append(path)
            continue
        for item in path.rglob("*"):
            if not item.is_file():
                continue
            if any(part in EXCLUDE_NAMES for part in item.parts):
                continue
            files.append(item)
    return sorted(set(files), key=lambda p: p.relative_to(ROOT).as_posix())


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    target = OUT / PACKAGE
    files = iter_files()
    manifest = {
        "package": PACKAGE,
        "status": "FROZEN_FOR_WORK_EXECUTION",
        "product_baseline": "PRODUCT_REQUIREMENT_BASELINE_STORAGE_MVP_RC1_V1.0",
        "expected_baseline": "STORAGE_EXPECTED_BASELINE_RC1_V0.1",
        "test_asset_branch": "test/storage-rc1-scenario-automation-v0.1",
        "test_asset_head": os.environ.get("GITHUB_SHA", "LOCAL_BUILD"),
        "sut_package": "STORAGE_PRODUCT_MVP_RC1_JOINT_PACKAGE_20260924.zip",
        "sut_sha256": "b6f48ad5447ad82dd74ccbe31ff650c4f0eb6b64791e65812c0e19dc1c529017",
        "mock_ids": [f"M{i:02d}" for i in range(1, 23)],
        "system_cases": [f"SYS-{i:03d}" for i in range(1, 16)],
        "interface_cases": [f"ITF-{i:03d}" for i in range(1, 33)],
        "integration_cases": [f"INT-{i:02d}" for i in range(1, 13)],
        "golden_paths": ["GOLDEN-A", "GOLDEN-B", "GOLDEN-C"],
        "files": [p.relative_to(ROOT).as_posix() for p in files],
    }

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        info = zipfile.ZipInfo("PACKAGE_MANIFEST.json", FIXED_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        for path in files:
            rel = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(rel, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())

    sha = digest(target)
    sha_file = OUT / f"{PACKAGE}.sha256"
    sha_file.write_text(f"{sha}  {PACKAGE}\n", encoding="utf-8")
    print(f"PACKAGE={target}")
    print(f"SHA256={sha}")
    print(f"FILES={len(files) + 1}")


if __name__ == "__main__":
    main()
