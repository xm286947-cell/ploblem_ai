from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "HARDWARE_R1_E2E_VALIDATION_ca310a5062c8"
INCLUDE_ROOTS = ("services", "repositories", "runtime", "models", "parser", "contracts", "quality_knowledge", "knowledge_production", "schema", "prompts", "config/runtime")
INCLUDE_FILES = (
    "requirements.txt", "scripts/hardware_case_web_start.py", "scripts/hardware_r1_e2e_validation_start.py",
    "START_HARDWARE_R1_E2E_VALIDATION.command", "START_HARDWARE_R1_E2E_VALIDATION.bat",
    "config/hardware_r1_e2e_validation.example.json", "docs/product/HARDWARE_R1_SINGLE_PACKAGE_E2E_VALIDATION.md",
)
EXCLUDED_PARTS = {".git", "__pycache__", ".pytest_cache", "node_modules", "dist", "build", "releases", "tests", "test", "data", "sources", "output", "artifacts"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".docx", ".doc", ".zip", ".log"}
SENSITIVE_NAMES = {".env", ".env.local", "model.local.yaml", "model.local.yml", "agent.local.yaml", "agent.local.yml"}
SENSITIVE_NAMES.add("model.yaml")

ABSOLUTE_MACHINE_PATH = re.compile(
    rb"(?i)(?:/Users/[A-Za-z0-9._-]+/|/home/[A-Za-z0-9._-]+/|"
    rb"[A-Z]:[\\/](?:Users|Documents and Settings)[\\/][^\\/\r\n\"']+[\\/]|"
    rb"/private/tmp/)"
)
SECRET_LITERAL_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("PRIVATE_KEY", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("OPENAI_STYLE_KEY", re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("GITHUB_PAT", re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("GITHUB_TOKEN", re.compile(rb"\bgh[opusr]_[A-Za-z0-9]{20,}\b")),
    ("AWS_ACCESS_KEY", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("GOOGLE_API_KEY", re.compile(rb"\bAIza[0-9A-Za-z_-]{30,}\b")),
)


def allowed(relative: Path) -> bool:
    name = relative.name.lower()
    safe_example = name in {"model.local.example.yaml", "model.local.hardware_case.example.yaml"}
    return not (
        any(part.lower() in EXCLUDED_PARTS for part in relative.parts)
        or relative.suffix.lower() in EXCLUDED_SUFFIXES
        or name in SENSITIVE_NAMES
        or (".local." in name and not safe_example)
        or ".secret." in name
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    # Git may check out text assets with platform-specific CRLF conversion.
    # Canonicalize every UTF-8 text file (not only known source extensions) so
    # the same source commit yields byte-identical ZIPs on Windows and macOS.
    if b"\x00" not in data:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            if b"\r" in data:
                data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            if path.suffix.lower() == ".bat":
                data = data.replace(b"\n", b"\r\n")
    return data


def source_commit() -> str:
    configured = os.getenv("GITHUB_SHA")
    if configured:
        return configured
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    override = os.getenv("HARDWARE_R1_E2E_SOURCE_COMMIT")
    if dirty and not override:
        raise SystemExit("SOURCE_COMMIT_UNBOUND_DIRTY_TREE; set HARDWARE_R1_E2E_SOURCE_COMMIT=LOCAL_WORKTREE only for local smoke builds")
    return override or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "releases")
    args = parser.parse_args()
    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    for root_name in INCLUDE_ROOTS:
        root = ROOT / root_name
        if not root.is_dir():
            raise SystemExit(f"MISSING_REQUIRED_ROOT={root_name}")
        for path in root.rglob("*"):
            if path.is_file():
                relative = path.relative_to(ROOT)
                if allowed(relative):
                    files[relative.as_posix()] = path
    for name in INCLUDE_FILES:
        path = ROOT / name
        if not path.is_file():
            raise SystemExit(f"MISSING_REQUIRED_FILE={name}")
        files[name] = path
    if any(not allowed(Path(name)) for name in files):
        raise SystemExit("PACKAGE_SECURITY_FILTER_FAILED")
    for name, path in files.items():
        data = package_bytes(path)
        if ABSOLUTE_MACHINE_PATH.search(data):
            raise SystemExit(f"PACKAGE_ABSOLUTE_PATH_FOUND={name}")
        for secret_name, pattern in SECRET_LITERAL_PATTERNS:
            if pattern.search(data):
                raise SystemExit(
                    f"PACKAGE_SECRET_LITERAL_FOUND={secret_name}:{name}"
                )
    commit = source_commit()
    inventory = [{"path": name, "sha256": hashlib.sha256(package_bytes(path)).hexdigest(), "size": len(package_bytes(path))} for name, path in sorted(files.items())]
    manifest = {
        "package_id": PACKAGE, "package_type": "SINGLE_PACKAGE_E2E_VALIDATION", "source_product_base": "ca310a5062c8fa15e6a00d22c718fdc7504192c8",
        "source_commit": commit, "certification_scope": "HARDWARE_R1_E2E_VALIDATION", "release_claim": "NOT_A_PRODUCT_RELEASE",
        "entrypoint": {"windows": "START_HARDWARE_R1_E2E_VALIDATION.bat", "macos": "START_HARDWARE_R1_E2E_VALIDATION.command"},
        "existing_application": "create_p0_app / HARDWARE_CASE domain", "web_required": True,
        "automatic_review": False, "automatic_publish": False, "real_provider_in_ci": False, "real_docx_in_ci": False,
        "manual_acceptance_scope": "FOUR_DOC_REAL_E2E",
        "package_security": {
            "sensitive_file_filter": True,
            "secret_literal_scan": True,
            "absolute_machine_path_scan": True,
        },
        "files": inventory,
    }
    payload = {**files, "PACKAGE_MANIFEST.json": None}
    archive = output / f"{PACKAGE}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
        for name in sorted(payload):
            data = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n" if name == "PACKAGE_MANIFEST.json" else package_bytes(payload[name])
            info = zipfile.ZipInfo(f"{PACKAGE}/{name}", date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (0o100755 if name.endswith(".command") else 0o100644) << 16
            bundle.writestr(info, data)
    print(f"PACKAGE={archive}")
    print(f"SOURCE_COMMIT={commit}")
    print(f"PACKAGE_SHA256={sha256(archive)}")
    print(f"PACKAGE_SIZE={archive.stat().st_size}")
    print("PACKAGE_SECURITY_FILTER=PASS")
    print("PACKAGE_SECRET_LITERAL_SCAN=PASS")
    print("PACKAGE_ABSOLUTE_PATH_SCAN=PASS")
    print("MANUAL_ACCEPTANCE_SCOPE=FOUR_DOC_REAL_E2E")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
