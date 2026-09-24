from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

PACKAGE_ROOT = "STORAGE_PRODUCT_MVP_RC1"
EXPECTED_PACKAGE_ID = "STORAGE-RC1-PACKAGE-DEFECT-115-FIX-CANDIDATE-20260925-R5"
EXPECTED_BUILD_VERSION = "storage-rc1-r5-builder-v1.0"
EXPECTED_R4A_SHA256 = "4de6586bc23a1105e4004faf949897a595a9c38b5382cb3e99d75c15111ddb60"
EXPECTED_MANIFEST_PATHS = 256
REQUIRED = {
    "start_test.sh",
    "run_server_test.sh",
    "run_product_test.sh",
    "selfcheck.sh",
    "run_windows.bat",
    "RELEASE_MANIFEST.json",
    "FILE_SHA256SUMS.txt",
    "config/product_test.env",
    "config/model.local.yaml",
    "scripts/port_guard.py",
    "scripts/port_regression.py",
}
SHELLS = {"start_test.sh", "run_server_test.sh", "run_product_test.sh", "selfcheck.sh"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for c in iter(lambda: f.read(1024 * 1024), b""):
            h.update(c)
    return h.hexdigest()


def safe(name: str) -> bool:
    p = PurePosixPath(name)
    return not (
        name.startswith(("/", "\\"))
        or "\\" in name
        or ".." in p.parts
    )


def parse_hashes(root: Path):
    rows = [
        x
        for x in (root / "FILE_SHA256SUMS.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        if x.strip()
    ]
    if len(rows) != EXPECTED_MANIFEST_PATHS:
        raise SystemExit(
            "PACKAGE_GATE=FAIL reason=manifest_count "
            f"expected={EXPECTED_MANIFEST_PATHS} actual={len(rows)}"
        )
    for row in rows:
        exp, rel = row.split(maxsplit=1)
        rel = rel.strip().lstrip("*").removeprefix("./")
        p = root / rel
        if not p.is_file():
            raise SystemExit(f"PACKAGE_GATE=FAIL reason=manifest_missing path={rel}")
        if sha256(p).lower() != exp.lower():
            raise SystemExit(f"PACKAGE_GATE=FAIL reason=manifest_mismatch path={rel}")
    return len(rows)


def inspect_zip(zippath: Path):
    with zipfile.ZipFile(zippath) as zf:
        infos = zf.infolist()
        names = [i.filename for i in infos]
        if len(names) != len(set(names)):
            raise SystemExit("PACKAGE_GATE=FAIL reason=duplicate_zip_path")
        if any(not safe(n) for n in names):
            raise SystemExit("PACKAGE_GATE=FAIL reason=illegal_zip_path")
        bad = zf.testzip()
        if bad:
            raise SystemExit(f"PACKAGE_GATE=FAIL reason=crc path={bad}")
        rels = {
            n[len(PACKAGE_ROOT) + 1 :]
            for n in names
            if n.startswith(PACKAGE_ROOT + "/")
        }
        missing = sorted(REQUIRED - rels)
        if missing:
            raise SystemExit(
                f"PACKAGE_GATE=FAIL reason=required_missing paths={missing}"
            )
        for info in infos:
            if not info.filename.endswith(".sh"):
                continue
            mode = (info.external_attr >> 16) & 0xFFFF
            if (
                stat.S_IFMT(mode) != stat.S_IFREG
                or stat.S_IMODE(mode) != 0o755
            ):
                raise SystemExit(
                    "PACKAGE_GATE=FAIL reason=shell_zip_mode "
                    f"path={info.filename} mode={oct(mode)}"
                )
        chinese = [
            i for i in infos if any(ord(ch) > 127 for ch in i.filename)
        ]
        if not chinese:
            raise SystemExit("PACKAGE_GATE=FAIL reason=utf8_path_missing")
        for info in chinese:
            if not (info.flag_bits & 0x800):
                raise SystemExit(
                    f"PACKAGE_GATE=FAIL reason=utf8_flag path={info.filename}"
                )
        return len(infos), len(chinese)


def validate_extracted(
    root: Path, expected_branch: str | None, expected_commit: str | None
):
    count = parse_hashes(root)
    manifest = json.loads(
        (root / "RELEASE_MANIFEST.json").read_text(encoding="utf-8")
    )
    for key in ("source_branch", "source_commit", "build_script_version"):
        if not str(manifest.get(key) or "").strip():
            raise SystemExit(
                f"PACKAGE_GATE=FAIL reason=provenance_missing key={key}"
            )
    if manifest.get("source_branch") == "N/A_PACKAGE_DERIVED_SOURCE":
        raise SystemExit("PACKAGE_GATE=FAIL reason=untraceable_source")
    if not re.fullmatch(
        r"[0-9a-f]{40}", str(manifest.get("source_commit") or "")
    ):
        raise SystemExit("PACKAGE_GATE=FAIL reason=source_commit_format")
    if expected_branch and manifest.get("source_branch") != expected_branch:
        raise SystemExit("PACKAGE_GATE=FAIL reason=source_branch_mismatch")
    if expected_commit and manifest.get("source_commit") != expected_commit.lower():
        raise SystemExit("PACKAGE_GATE=FAIL reason=source_commit_mismatch")
    if manifest.get("package_id") != EXPECTED_PACKAGE_ID:
        raise SystemExit("PACKAGE_GATE=FAIL reason=package_id_mismatch")
    if manifest.get("build_script_version") != EXPECTED_BUILD_VERSION:
        raise SystemExit("PACKAGE_GATE=FAIL reason=build_version_mismatch")

    provenance = manifest.get("build_provenance") or {}
    if provenance.get("base_package_sha256") != EXPECTED_R4A_SHA256:
        raise SystemExit("PACKAGE_GATE=FAIL reason=base_sha_mismatch")
    if (
        provenance.get("base_package_role")
        != "FAILED_RETIRED_FROZEN_INPUT_NOT_SOURCE_OF_TRUTH"
    ):
        raise SystemExit("PACKAGE_GATE=FAIL reason=base_role_mismatch")
    if provenance.get("r4a_status") != "FAILED_RETIRED":
        raise SystemExit("PACKAGE_GATE=FAIL reason=r4a_status_mismatch")

    fix = manifest.get("defect_115_fix") or {}
    if fix.get("release_ceiling") != "READY_FOR_PLATFORM_RETEST":
        raise SystemExit("PACKAGE_GATE=FAIL reason=release_ceiling_mismatch")
    if fix.get("r4a_status") != "FAILED_RETIRED":
        raise SystemExit("PACKAGE_GATE=FAIL reason=r4a_fix_status_mismatch")
    ownership = fix.get("launcher_port_ownership") or {}
    if (
        ownership.get("defect_id")
        != "DEFECT-STG-LAUNCHER-PORT-OWNERSHIP-001"
    ):
        raise SystemExit("PACKAGE_GATE=FAIL reason=port_defect_id_missing")
    if ownership.get("health_only_success_forbidden") is not True:
        raise SystemExit("PACKAGE_GATE=FAIL reason=health_only_not_forbidden")
    if ownership.get("storage_web_port_configurable") is not True:
        raise SystemExit("PACKAGE_GATE=FAIL reason=web_port_not_configurable")
    if ownership.get("product_e2e_on_port_conflict") != "NOT_RUN":
        raise SystemExit("PACKAGE_GATE=FAIL reason=port_conflict_e2e_contract")
    if "terminated and waited" not in str(ownership.get("shutdown_rule") or ""):
        raise SystemExit("PACKAGE_GATE=FAIL reason=shutdown_ownership_contract")

    automation = fix.get("port_automation") or {}
    for case in (
        "TEST-PORT-01",
        "TEST-PORT-02",
        "TEST-PORT-03",
        "TEST-PORT-04",
        "TEST-PORT-05",
    ):
        if not automation.get(case):
            raise SystemExit(
                f"PACKAGE_GATE=FAIL reason=port_automation_missing case={case}"
            )

    for rel in SHELLS:
        text = (root / rel).read_text(encoding="utf-8")
        if re.search(
            r"(?m)(^|[;&|]\s*)\./[^\s]+\.sh(?:\s|$)",
            text,
        ):
            raise SystemExit(
                "PACKAGE_GATE=FAIL reason=implicit_shell_exec_dependency "
                f"path={rel}"
            )

    launcher = (root / "run_product_test.sh").read_text(encoding="utf-8")
    launcher_required_tokens = (
        'wait "$p"',
        "PRODUCT_E2E=NOT_RUN",
        "scripts/port_guard.py check-free",
        "scripts/port_guard.py wait-owned",
        "STORAGE_WEB_PORT",
        "18000",
        "18001",
    )
    for token in launcher_required_tokens:
        if token not in launcher:
            raise SystemExit(
                "PACKAGE_GATE=FAIL reason=port_ownership_launcher_contract "
                f"missing={token}"
            )

    guard = (root / "scripts/port_guard.py").read_text(encoding="utf-8")
    guard_required_tokens = (
        "PORT_CONFLICT",
        "SPAWNED_PROCESS_EXITED",
        "HEALTH_OWNERSHIP_MISMATCH",
        "PID_PORT_OWNERSHIP",
        "check-free",
        "wait-owned",
    )
    for token in guard_required_tokens:
        if token not in guard:
            raise SystemExit(
                "PACKAGE_GATE=FAIL reason=port_guard_contract "
                f"missing={token}"
            )
    return count, manifest


def extract_python(zippath: Path, out: Path):
    with zipfile.ZipFile(zippath) as zf:
        zf.extractall(out)


def extract_unzip(zippath: Path, out: Path):
    if not shutil.which("unzip"):
        raise SystemExit("PACKAGE_GATE=FAIL reason=unzip_missing")
    subprocess.run(["unzip", "-q", str(zippath), "-d", str(out)], check=True)


def run_linux_matrix(
    zippath: Path,
    expected_branch: str | None,
    expected_commit: str | None,
    run_selfcheck: bool,
):
    results = []
    for label, extractor in (
        ("PYTHON_ZIPFILE", extract_python),
        ("LINUX_UNZIP", extract_unzip),
    ):
        with tempfile.TemporaryDirectory(prefix="storage-r5-gate-") as td:
            out = Path(td)
            extractor(zippath, out)
            root = out / PACKAGE_ROOT
            count, _ = validate_extracted(
                root, expected_branch, expected_commit
            )
            modes = {
                p.name: oct(stat.S_IMODE(p.stat().st_mode))
                for p in root.glob("*.sh")
            }
            env = os.environ.copy()
            env["STORAGE_TEST_NO_WAIT"] = "1"
            env["STORAGE_TEST_RESET_DATA"] = "1"
            p = subprocess.run(
                ["bash", "start_test.sh", "mock"],
                cwd=root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            print(p.stdout)
            if p.returncode != 0:
                raise SystemExit(
                    "PACKAGE_GATE=FAIL reason=official_launcher "
                    f"extractor={label} rc={p.returncode}"
                )
            results.append((label, count, modes))
            if run_selfcheck and label == "PYTHON_ZIPFILE":
                p = subprocess.run(
                    ["bash", "selfcheck.sh"],
                    cwd=root,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                print(p.stdout)
                if p.returncode != 0:
                    raise SystemExit(
                        f"PACKAGE_GATE=FAIL reason=selfcheck rc={p.returncode}"
                    )
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True, type=Path)
    ap.add_argument("--expected-sha256")
    ap.add_argument("--source-branch")
    ap.add_argument("--source-commit")
    ap.add_argument("--linux-matrix", action="store_true")
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()
    z = args.package.resolve()
    actual = sha256(z)
    if args.expected_sha256 and actual != args.expected_sha256.lower():
        raise SystemExit(
            "PACKAGE_GATE=FAIL reason=sha "
            f"expected={args.expected_sha256} actual={actual}"
        )
    entries, chinese = inspect_zip(z)
    with tempfile.TemporaryDirectory(prefix="storage-r5-contract-") as td:
        out = Path(td)
        extract_python(z, out)
        root = out / PACKAGE_ROOT
        count, _ = validate_extracted(
            root, args.source_branch, args.source_commit
        )
    print(f"PACKAGE_INTEGRITY=PASS sha256={actual} entries={entries}")
    print(f"MANIFEST=PASS paths={count}")
    print(f"UTF8_PATH=PASS chinese_entries={chinese}")
    print("ZIP_ENTRY_METADATA=PASS shell_mode=0755")
    print("PORT_OWNERSHIP_CONTRACT=PASS")
    if args.linux_matrix:
        for label, count, modes in run_linux_matrix(
            z,
            args.source_branch,
            args.source_commit,
            args.selfcheck,
        ):
            print(
                f"{label}_FRESH_EXTRACT=PASS "
                f"manifest_paths={count} shell_modes={modes}"
            )
        print("LINUX_OFFICIAL_LAUNCHER=PASS")
        print("NO_CHMOD_REQUIRED=PASS")
        print("NO_MANUAL_REPAIR=PASS")
    print("PACKAGE_GATE=PASS")


if __name__ == "__main__":
    main()
