from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "products" / "storage_rc1"
DIST = ROOT / "dist"
PACKAGE_ROOT_NAME = "STORAGE_PRODUCT_MVP_RC1"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def source_commit() -> str:
    value = os.environ.get("GITHUB_SHA", "").strip()
    if value:
        return value
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def copy_tree(source: Path, target: Path) -> None:
    if not source.exists():
        raise SystemExit(f"missing package dependency: {source}")
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".pytest_cache", ".DS_Store"
        ),
    )


def copy_file(source: Path, target: Path) -> None:
    if not source.is_file():
        raise SystemExit(f"missing package dependency: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def verify_release(package_root: Path) -> dict:
    release = package_root / "knowledge_release" / "current"
    manifest_path = release / "release_manifest.json"
    if not manifest_path.is_file():
        raise SystemExit("KNOWLEDGE_RELEASE_NOT_FOUND")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest.get("files") or []:
        name = entry.get("path")
        expected = entry.get("sha256")
        if not name or not expected:
            continue
        path = release / name
        if not path.is_file() or sha256(path) != expected:
            raise SystemExit(f"KNOWLEDGE_RELEASE_HASH_MISMATCH:{name}")
    return manifest


def scan_secrets(package_root: Path) -> None:
    forbidden = (
        "sk-proj-",
        "BEGIN PRIVATE KEY",
        "AKIA",
    )
    for path in package_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".zip", ".sqlite3"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for marker in forbidden:
            if marker in text:
                raise SystemExit(f"SECRET_SCAN_FAILED:{path.relative_to(package_root)}:{marker}")


def write_hash_manifest(package_root: Path) -> None:
    rows = []
    for path in sorted(package_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(package_root).as_posix()
        if rel in {"FILE_SHA256SUMS.txt", "PACKAGE_MANIFEST.json"}:
            continue
        rows.append(f"{sha256(path)}  {rel}")
    (package_root / "FILE_SHA256SUMS.txt").write_text(
        "\n".join(rows) + "\n", encoding="utf-8"
    )


def build() -> tuple[Path, Path, Path]:
    commit = source_commit()
    date = os.environ.get("STORAGE_R6_BUILD_DATE", "").strip() or datetime.now(timezone.utc).strftime("%Y%m%d")
    package_id = f"STORAGE-RC1-R6-COMPLETE-TEST-CANDIDATE-{date}"
    zip_name = f"STORAGE_PRODUCT_MVP_RC1_R6_COMPLETE_TEST_CANDIDATE_{date}.zip"

    work = DIST / "_storage_r6_complete"
    if work.exists():
        shutil.rmtree(work)
    package_root = work / PACKAGE_ROOT_NAME
    copy_tree(PRODUCT, package_root)

    # Unified Knowledge Production is packaged as the shared capability, not copied
    # into Storage domain code. Storage only adds the product bridge/consumer.
    copy_tree(ROOT / "knowledge_production", package_root / "knowledge_production")
    copy_tree(ROOT / "repositories", package_root / "repositories")
    copy_tree(ROOT / "parser", package_root / "parser")

    # Preserve the existing launcher contract: unified Runtime remains under vendor.
    runtime_root = package_root / "vendor" / "unified_agent_runtime"
    copy_tree(ROOT / "runtime", runtime_root / "runtime")
    copy_file(
        ROOT / "config" / "runtime" / "agents" / "knowledge.production.extract.yaml",
        package_root / "config" / "runtime" / "agents" / "knowledge.production.extract.yaml",
    )
    copy_file(
        ROOT / "config" / "runtime" / "agents" / "knowledge.production.extract.yaml",
        runtime_root / "config" / "runtime" / "agents" / "knowledge.production.extract.yaml",
    )
    copy_tree(
        ROOT / "prompts" / "runtime" / "knowledge_production",
        package_root / "prompts" / "runtime" / "knowledge_production",
    )
    copy_tree(
        ROOT / "prompts" / "runtime" / "knowledge_production",
        runtime_root / "prompts" / "runtime" / "knowledge_production",
    )
    (runtime_root / "RUNTIME_COMMIT").write_text(commit + "\n", encoding="utf-8")

    release = verify_release(package_root)
    scan_secrets(package_root)

    manifest = {
        "package_id": package_id,
        "package_type": "PRODUCT_TEST_CANDIDATE",
        "product": "Storage RC1",
        "assembly": "Golden A + Golden B + Golden C",
        "source_commit": commit,
        "base_commit": "cb4e7e3d0e245d56ed507f54d86110f1322884f7",
        "knowledge_release_version": release.get("knowledge_release_version"),
        "knowledge_snapshot_hash": release.get("snapshot_hash"),
        "knowledge_product_packaged": True,
        "device_lifecycle_gate": "DRAFT_TO_FORMAL_READY",
        "runtime_observation_contract": "AVAILABLE_OR_EXPLICIT_UNKNOWN",
        "r5_changed": False,
        "product_gate_pass": "NOT_CLAIMED",
        "rc1_pass": "NOT_CLAIMED",
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    (package_root / "PACKAGE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_hash_manifest(package_root)

    DIST.mkdir(parents=True, exist_ok=True)
    zip_path = DIST / zip_name
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(package_root.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(work).as_posix())

    digest = sha256(zip_path)
    sha_path = DIST / f"{zip_name}.sha256"
    sha_path.write_text(f"{digest}  {zip_name}\n", encoding="utf-8")
    delivery = DIST / f"{zip_name}.DELIVERY_MANIFEST.json"
    delivery.write_text(
        json.dumps(
            {
                **manifest,
                "package": zip_name,
                "package_sha256": digest,
                "package_size": zip_path.stat().st_size,
                "fresh_extract_required": True,
                "next_status": "READY_FOR_FULL_PRODUCT_RETEST",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return zip_path, sha_path, delivery


if __name__ == "__main__":
    package, sha_file, delivery = build()
    print(f"PACKAGE={package}")
    print(f"SHA256={sha_file.read_text(encoding='utf-8').split()[0]}")
    print(f"SIZE={package.stat().st_size}")
    print(f"DELIVERY_MANIFEST={delivery}")
