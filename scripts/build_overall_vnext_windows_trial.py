"""Build a reproducible full-source Windows manual-trial package from HEAD."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(*args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout


def build(output_dir: Path) -> tuple[Path, Path, str]:
    commit = _git("rev-parse", "HEAD").decode().strip()
    if len(commit) != 40:
        raise RuntimeError("SOURCE_COMMIT_INVALID")
    archive = _git("archive", "--format=zip", "HEAD")
    source_archive_sha = _sha256(archive)
    short = commit[:7]
    package_name = f"OVERALL_VNEXT_WINDOWS_MANUAL_TRIAL_{short}_W2"
    output_dir.mkdir(parents=True, exist_ok=True)
    package_dir = output_dir / package_name
    zip_path = output_dir / f"{package_name}.zip"
    if package_dir.exists() or zip_path.exists():
        raise FileExistsError(f"OUTPUT_ALREADY_EXISTS:{package_name}")
    package_dir.mkdir()
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        source.extractall(package_dir)

    runtime_entry = package_dir / "runtime" / "__init__.py"
    if not runtime_entry.is_file():
        raise RuntimeError("SHARED_RUNTIME_SOURCE_MISSING")
    if (package_dir / "vendor" / "unified_agent_runtime" / "runtime" / "__init__.py").exists():
        raise RuntimeError("DUPLICATE_VENDOR_RUNTIME_IN_SOURCE")
    (package_dir / "RUNTIME_COMMIT").write_text(commit + "\n", encoding="utf-8")
    manifest = {
        "package_type": "WINDOWS_MANUAL_TRIAL_PACKAGE",
        "package_version": "W2",
        "dut_source_commit": commit,
        "source_archive_sha256": source_archive_sha,
        "runtime_binding": {
            "mode": "single_shared_package_root",
            "runtime_source": "runtime/__init__.py",
            "runtime_commit_marker": "RUNTIME_COMMIT",
            "duplicate_vendor_runtime": False,
        },
        "dependency_install": {
            "mode": "online_pip_unless_complete_wheelhouse_is_added",
            "wheelhouse_included": False,
            "note": "This build does not ship a partial wheelhouse. Windows manual startup requires PyPI access.",
        },
        "test_release_id": None,
        "prior_formal_product_gate": "NOT_INHERITED",
        "synthetic_data_only": True,
    }
    (package_dir / "OVERALL_VNEXT_WINDOWS_TRIAL_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    readme = """OVERALL VNext Windows Manual Trial — W2

Start: double-click START_OVERALL_VNEXT_WINDOWS.bat.
The launcher installs Python requirements, validates the pinned source commit and
the one shared Runtime import path, then starts the existing single FastAPI host
on port 8080 and opens http://127.0.0.1:8080/p0/issues.

This package does not include a complete Windows wheelhouse. First startup needs
PyPI access. It deliberately does not include a partial wheelhouse or a second
vendor/unified_agent_runtime tree. Do not copy another Runtime folder into it.

AI calls are not made during startup. Configure provider endpoint/key using the
environment variable names in config/runtime/model.yaml before testing AI flows.
The committed config contains placeholders/environment references, not secrets.
Demo data is synthetic and stored under %LOCALAPPDATA%\\OverallVNextDemo\\data.

To verify Runtime binding without starting the web server, run:
  .venv\\Scripts\\python.exe scripts\\overall_vnext_windows_start.py --check-runtime-only

This is a VNext manual-trial candidate. Prior release gates do not transfer to
this DUT commit. Windows native startup and TSE testing remain separate checks.
"""
    (package_dir / "WINDOWS_START_HERE.txt").write_text(readme, encoding="utf-8")

    files = sorted(p for p in package_dir.rglob("*") if p.is_file())
    checksums = "".join(
        f"{_sha256(path.read_bytes())}  {path.relative_to(package_dir).as_posix()}\n"
        for path in files
    )
    (package_dir / "SHA256SUMS.txt").write_text(checksums, encoding="utf-8")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=7) as bundle:
        for path in sorted(p for p in package_dir.rglob("*") if p.is_file()):
            member = zipfile.ZipInfo(
                f"{package_name}/{path.relative_to(package_dir).as_posix()}",
                date_time=(2026, 1, 1, 0, 0, 0),
            )
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = 0o100644 << 16
            bundle.writestr(member, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=7)
    with zipfile.ZipFile(zip_path) as bundle:
        bad_member = bundle.testzip()
        if bad_member:
            raise RuntimeError(f"ZIP_MEMBER_FAILED:{bad_member}")
        packaged_manifest = json.loads(bundle.read(f"{package_name}/OVERALL_VNEXT_WINDOWS_TRIAL_MANIFEST.json"))
        if packaged_manifest.get("dut_source_commit") != commit:
            raise RuntimeError("ZIP_DUT_BINDING_MISMATCH")
    return package_dir, zip_path, commit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    package_dir, zip_path, commit = build(args.output_dir.expanduser().resolve())
    print(f"DUT_SOURCE_COMMIT={commit}")
    print(f"PACKAGE_DIR={package_dir}")
    print(f"PACKAGE_ZIP={zip_path}")
    print(f"PACKAGE_SHA256={_sha256(zip_path.read_bytes())}")
    print("ZIP_INTEGRITY=PASS")
    print("INTERNAL_SHA256SUMS=PASS")
    print("WINDOWS_NATIVE_STARTUP=NOT_RUN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
