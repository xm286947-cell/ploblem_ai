from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

BUILD_SCRIPT_VERSION = "storage-rc1-r4-builder-v1.1"
EXPECTED_BASE_SHA256 = "a535a7cf741058ef687a6d65db83e18a32414e111c023517ae83c1a5fbac1b01"
PACKAGE_ROOT = "STORAGE_PRODUCT_MVP_RC1"
PACKAGE_ID = "STORAGE-RC1-PACKAGE-DEFECT-115-FIX-CANDIDATE-20260924-R4A"
PACKAGE_NAME = "STORAGE_PRODUCT_MVP_RC1_DEFECT_115_FIX_CANDIDATE_20260924_R4A.zip"
FIXED_TIME = (2026, 9, 24, 0, 0, 0)
OVERLAY_FILES = (
    "start_test.sh",
    "run_server_test.sh",
    "run_product_test.sh",
    "selfcheck.sh",
    "run_windows.bat",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_name(name: str) -> None:
    p = PurePosixPath(name)
    if name.startswith(("/", "\\")) or "\\" in name or ".." in p.parts:
        raise SystemExit(f"unsafe ZIP path: {name}")


def extract_verified(base_zip: Path, out: Path) -> Path:
    actual = sha256(base_zip)
    if actual != EXPECTED_BASE_SHA256:
        raise SystemExit(f"base package SHA mismatch: expected={EXPECTED_BASE_SHA256} actual={actual}")
    with zipfile.ZipFile(base_zip) as zf:
        names = [i.filename for i in zf.infolist()]
        if len(names) != len(set(names)):
            raise SystemExit("base package contains duplicate ZIP paths")
        for name in names:
            safe_name(name)
        bad = zf.testzip()
        if bad:
            raise SystemExit(f"base package CRC failure: {bad}")
        zf.extractall(out)
    root = out / PACKAGE_ROOT
    if not root.is_dir():
        raise SystemExit(f"package root missing: {PACKAGE_ROOT}")
    return root


def parse_manifest_paths(root: Path) -> list[str]:
    manifest = root / "FILE_SHA256SUMS.txt"
    rows = [x for x in manifest.read_text(encoding="utf-8").splitlines() if x.strip()]
    paths = []
    for lineno, row in enumerate(rows, 1):
        parts = row.split(maxsplit=1)
        if len(parts) != 2:
            raise SystemExit(f"malformed FILE_SHA256SUMS line {lineno}")
        rel = parts[1].strip().lstrip("*")
        if rel.startswith("./"):
            rel = rel[2:]
        safe_name(rel)
        paths.append(rel)
    if len(paths) != 254:
        raise SystemExit(f"manifest path count drift: expected=254 actual={len(paths)}")
    if len(paths) != len(set(paths)):
        raise SystemExit("manifest contains duplicate paths")
    return paths


def update_release_manifest(root: Path, source_branch: str, source_commit: str, base_name: str) -> None:
    if not source_branch.strip() or source_branch == "N/A_PACKAGE_DERIVED_SOURCE":
        raise SystemExit("SOURCE_BRANCH must be traceable")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", source_commit):
        raise SystemExit("SOURCE_COMMIT must be a full 40-hex Git commit")
    p = root / "RELEASE_MANIFEST.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["package_id"] = PACKAGE_ID
    data["status"] = "R4A_BUILT_PENDING_CROSS_PLATFORM_RELEASE_GATE"
    data["source_branch"] = source_branch
    data["source_commit"] = source_commit.lower()
    data["build_script_version"] = BUILD_SCRIPT_VERSION
    data["build_provenance"] = {
        "source_branch": source_branch,
        "source_commit": source_commit.lower(),
        "build_script_version": BUILD_SCRIPT_VERSION,
        "base_package": base_name,
        "base_package_sha256": EXPECTED_BASE_SHA256,
        "base_package_role": "FROZEN_INPUT_NOT_SOURCE_OF_TRUTH",
        "r3_status": "FAILED_RETIRED",
    }
    fix = data.setdefault("defect_115_fix", {})
    fix["r3_status"] = "FAILED_RETIRED"
    fix["r3_package_sha256"] = EXPECTED_BASE_SHA256
    fix["release_ceiling"] = "READY_FOR_PLATFORM_RETEST"
    fix["dc_002"] = {
        "status": "FIXED_IN_R4A_PENDING_CROSS_PLATFORM_RETEST",
        "root_cause": "R3 launcher semantics depended on the extractor restoring ZIP Unix executable bits. The same immutable R3 ZIP/SHA produced 0755 in one fresh-extract path and 0644 in another, while launchers invoked shell helpers as ./helper.sh.",
        "fix": "Linux/macOS official launch is bash start_test.sh mock|real and every shell-to-shell helper call is explicit bash. ZIP .sh entries remain Unix regular 0755 metadata, but executable-bit restoration is no longer a runtime dependency.",
        "manual_chmod_allowed": False,
    }
    fix["r4_launcher_contract"] = {
        "linux_macos_official": ["bash start_test.sh mock", "bash start_test.sh real"],
        "windows_official": "run_windows.bat",
        "shell_executable_bit_runtime_dependency": False,
        "shell_zip_metadata_required": "0755 contract only",
        "manual_chmod_allowed": False,
    }
    gates = data.setdefault("gates", {})
    gates["P0_PACKAGE_INTEGRITY"] = "PENDING_R4A_EXTERNAL_GATE"
    gates["LINUX_LAUNCHER_PACKAGE_SMOKE"] = "PENDING_R4A_EXTERNAL_GATE"
    gates["MACOS_CLEAN_MACHINE"] = "NOT_RUN"
    gates["WINDOWS_CLEAN_MACHINE"] = "NOT_RUN"
    gates["R4_PACKAGE_CONTRACT_GATE"] = "PENDING_EXTERNAL_EXECUTION"
    gates["R4_CROSS_PLATFORM_GATE"] = "PENDING_EXTERNAL_EXECUTION"
    gates["RC1_PACKAGE_READY"] = "NO"
    data["test_gate"] = "NOT_CLAIMED"
    data["product_gate"] = "NOT_CLAIMED"
    data["final_rc1"] = "NOT_CLAIMED"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rewrite_sha_manifest(root: Path, paths: list[str]) -> None:
    rows = []
    for rel in paths:
        target = root / rel
        if not target.is_file():
            raise SystemExit(f"manifest path missing before build: {rel}")
        rows.append(f"{sha256(target)}  {rel}")
    (root / "FILE_SHA256SUMS.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")


def write_zip(root: Path, target: Path) -> None:
    files = sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix())
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            rel = path.relative_to(root).as_posix()
            arc = f"{PACKAGE_ROOT}/{rel}"
            safe_name(arc)
            info = zipfile.ZipInfo(arc, FIXED_TIME)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o100755 if rel.endswith(".sh") else 0o100644
            info.external_attr = mode << 16
            zf.writestr(info, path.read_bytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-zip", required=True, type=Path)
    ap.add_argument("--overlay-dir", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--source-branch", required=True)
    ap.add_argument("--source-commit", required=True)
    args = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="storage-r4-build-") as td:
        root = extract_verified(args.base_zip.resolve(), Path(td))
        paths = parse_manifest_paths(root)
        for rel in OVERLAY_FILES:
            src = args.overlay_dir / rel
            if not src.is_file():
                raise SystemExit(f"overlay missing: {rel}")
            shutil.copy2(src, root / rel)
        update_release_manifest(root, args.source_branch, args.source_commit, args.base_zip.name)
        rewrite_sha_manifest(root, paths)
        target = args.out_dir / PACKAGE_NAME
        write_zip(root, target)
    print("TASK=STORAGE-DELIVERY-QUALITY-HARDENING-001")
    print(f"PACKAGE_ID={PACKAGE_ID}")
    print(f"PACKAGE={target}")
    print(f"SHA256={sha256(target)}")
    print(f"SOURCE_BRANCH={args.source_branch}")
    print(f"SOURCE_COMMIT={args.source_commit.lower()}")
    print(f"BUILD_SCRIPT_VERSION={BUILD_SCRIPT_VERSION}")


if __name__ == "__main__":
    main()
