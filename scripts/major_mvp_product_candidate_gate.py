"""Verify candidate ZIP contents, source identity, and data/secret exclusions."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath


REQUIRED_FILES = {
    "main.py",
    "requirements.txt",
    "requirements-major-mvp-product.txt",
    "scripts/major_mvp_product_start.py",
    "START_MAJOR_MVP.bat",
    "START_MAJOR_MVP.command",
    "START_MAJOR_MVP.sh",
    "quality_knowledge/web/p0_app.py",
    "quality_knowledge/web/templates/major_production.html",
    "quality_knowledge/web/templates/p0_cases.html",
    "quality_knowledge/web/templates/p0_issue_detail.html",
    "quality_knowledge/web/static/major_production.js",
    "quality_knowledge/web/static/p0_issue_detail.js",
    "quality_knowledge/web/static/p0_case_detail.js",
    "services/historical_case_contract.py",
    "quality_knowledge/repeat_risk/__init__.py",
}
FORBIDDEN_SUFFIXES = {
    ".db", ".doc", ".docx", ".env", ".key", ".log", ".p12", ".pem",
    ".pfx", ".pkl", ".pickle", ".sqlite", ".sqlite3", ".xls", ".xlsx",
    ".xlsm", ".pdf",
}
FORBIDDEN_PARTS = {
    ".pytest_cache", "__pycache__", "input", "knowledge", "output", "outputs",
    "samples", "test_assets", "test_data", "testdata", "tests",
}
LOCAL_PATH = re.compile(
    rb"(?:/Users/[^\s\"'<>]+|/home/[^\s\"'<>]+|/private/(?:tmp|var)/[^\s\"'<>]+|/tmp/[^\s\"'<>]+|[A-Za-z]:\\(?:Users|Temp|Windows\\Temp)\\[^\s\"'<>]+)"
)
LITERAL_SECRET = re.compile(
    r"(?im)^\s*(?:api[-_]?key|secret|client[-_]?secret|password|access[-_]?token|refresh[-_]?token)\s*:"
)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def verify_candidate(archive_path: str) -> dict[str, str]:
    archive = Path(archive_path).resolve()
    sidecar = archive.with_suffix(archive.suffix + ".sha256")
    if not archive.is_file() or not sidecar.is_file():
        raise ValueError("ZIP_OR_SHA256_SIDECAR_MISSING")
    checksum_fields = sidecar.read_text(encoding="ascii").strip().split()
    if len(checksum_fields) != 2 or checksum_fields[1] != archive.name:
        raise ValueError("SHA256_SIDECAR_FORMAT_INVALID")
    zip_digest = sha256(archive.read_bytes())
    if checksum_fields[0] != zip_digest:
        raise ValueError("SHA256_MISMATCH")

    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        if zf.testzip() is not None:
            raise ValueError("ZIP_INTEGRITY_FAILED")
        roots = {PurePosixPath(name).parts[0] for name in names if PurePosixPath(name).parts}
        if len(roots) != 1:
            raise ValueError("PACKAGE_ROOT_INVALID")
        package_root = next(iter(roots))
        manifest_name = f"{package_root}/PACKAGE_MANIFEST.json"
        if manifest_name not in names:
            raise ValueError("MANIFEST_MISSING")
        manifest = json.loads(zf.read(manifest_name))
        source_commit = str(manifest.get("source_commit") or "")
        if manifest.get("package_id") != package_root:
            raise ValueError("PACKAGE_ID_MISMATCH")
        if package_root != f"MAJOR_MVP_PRODUCT_CANDIDATE_{source_commit[:12]}":
            raise ValueError("SOURCE_SHA_IN_PACKAGE_NAME_MISMATCH")
        source_branch = str(manifest.get("source_branch") or "").strip()
        if not source_branch:
            raise ValueError("SOURCE_BRANCH_MISSING")
        if manifest.get("source_base") != "73e9db071c55ae056573982b70db9480be838353":
            raise ValueError("SOURCE_BASE_MISMATCH")
        if manifest.get("source_dirty") is not False:
            raise ValueError("SOURCE_TREE_NOT_CLEAN_AT_BUILD")

        payload_names = {
            name[len(package_root) + 1 :]
            for name in names
            if name.startswith(package_root + "/") and name != manifest_name
        }
        listed = set(manifest.get("files") or [])
        digests = manifest.get("file_sha256") or {}
        if payload_names != listed or set(digests) != listed:
            raise ValueError("MANIFEST_FILE_LIST_MISMATCH")
        missing = sorted(REQUIRED_FILES - listed)
        if missing:
            raise ValueError("REQUIRED_PACKAGE_FILES_MISSING=" + ",".join(missing))

        for relative in sorted(listed):
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"UNSAFE_ARCHIVE_PATH={relative}")
            if path.suffix.lower() in FORBIDDEN_SUFFIXES or FORBIDDEN_PARTS.intersection(path.parts):
                raise ValueError(f"FORBIDDEN_FILE_INCLUDED={relative}")
            content = zf.read(f"{package_root}/{relative}")
            if digests[relative] != sha256(content):
                raise ValueError(f"FILE_SHA256_MISMATCH={relative}")
            if LOCAL_PATH.search(content):
                raise ValueError(f"LOCAL_ABSOLUTE_PATH={relative}")
            if path.suffix.lower() in {".yaml", ".yml", ".json", ".toml", ".ini", ".env"}:
                try:
                    decoded = content.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise ValueError(f"INVALID_TEXT_ENCODING={relative}") from error
                if LITERAL_SECRET.search(decoded):
                    raise ValueError(f"LITERAL_SECRET_FIELD={relative}")

        launchers = manifest.get("launchers") or {}
        launcher_result = all(
            launchers.get(platform) in listed
            for platform in ("windows", "macos", "posix")
        )
        if not launcher_result:
            raise ValueError("LAUNCHER_MANIFEST_INVALID")
        launcher_contracts = {
            "START_MAJOR_MVP.bat": (b".venv\\Scripts\\python.exe", b"requirements-major-mvp-product.txt"),
            "START_MAJOR_MVP.command": (b"START_MAJOR_MVP.sh",),
            "START_MAJOR_MVP.sh": (b".venv/bin/python", b"requirements-major-mvp-product.txt"),
        }
        for launcher, markers in launcher_contracts.items():
            payload = zf.read(f"{package_root}/{launcher}")
            if any(marker not in payload for marker in markers):
                raise ValueError(f"LAUNCHER_STATIC_CHECK_FAILED={launcher}")

    return {
        "ZIP_EXISTS": "YES",
        "SHA256_MATCH": "YES",
        "MANIFEST_EXISTS": "YES",
        "WINDOWS_LAUNCHER_EXISTS": "YES",
        "MACOS_LAUNCHER_EXISTS": "YES",
        "POSIX_LAUNCHER_EXISTS": "YES",
        "MAJOR_WEB_ASSETS_PRESENT": "YES",
        "REPEAT_RISK_ASSETS_PRESENT": "YES",
        "HISTORICAL_CASE_CODE_PRESENT": "YES",
        "REAL_DB_INCLUDED": "NO",
        "SECRET_INCLUDED": "NO",
        "LOCAL_ABSOLUTE_PATH": "NO",
        "SOURCE_SHA_IN_MANIFEST": "CORRECT",
        "SOURCE_COMMIT": source_commit,
        "PACKAGE_ID": package_root,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    args = parser.parse_args()
    try:
        result = verify_candidate(args.archive)
    except (OSError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as error:
        print("PACKAGE_GATE=FAIL")
        print(f"BLOCKER={error}")
        return 1
    for key, value in result.items():
        print(f"{key}={value}")
    print("PACKAGE_GATE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
