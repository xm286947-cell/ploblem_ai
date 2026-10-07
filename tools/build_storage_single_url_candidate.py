from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "products" / "storage_rc1"
RUNTIME_PIN = "9e36eeb0237459b884ee0d3e663ccf5833bfc685"
DEFAULT_OUTPUT = ROOT / "dist" / "single_url_candidate"
PACKAGE_ROOT = "STORAGE_PRODUCT_MVP_RC1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_tree(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise SystemExit(f"PACKAGE_INPUT_MISSING:{source}")
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".pytest_cache", ".DS_Store", ".venv",
            "knowledge_service.local.json", "*.sqlite", "*.sqlite3", "*.db",
            "provider_runtime.log", "launcher_latest.log", "storage_app.log",
        ),
    )


def copy_file(source: Path, target: Path) -> None:
    if not source.is_file():
        raise SystemExit(f"PACKAGE_INPUT_MISSING:{source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def write_file_hashes(root: Path) -> None:
    rows = [f"{sha256(path)}  {path.relative_to(root).as_posix()}"
            for path in sorted(root.rglob("*")) if path.is_file()]
    (root / "FILE_SHA256SUMS.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")


def build(output_dir: Path, build_date: str | None = None) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    date = build_date or datetime.now(timezone.utc).strftime("%Y%m%d")
    package_id = f"STORAGE-SINGLE-URL-KNOWLEDGE-CANDIDATE-{source_commit[:12]}-{date}"
    zip_name = f"{package_id}.zip"

    work = Path(tempfile.mkdtemp(prefix="storage-single-url-package-", dir=output_dir))
    package_root = work / PACKAGE_ROOT
    try:
        copy_tree(PRODUCT, package_root)
        copy_tree(ROOT / "knowledge_production", package_root / "knowledge_production")
        copy_tree(ROOT / "repositories", package_root / "repositories")
        copy_tree(ROOT / "parser", package_root / "parser")
        copy_tree(ROOT / "services", package_root / "services")
        copy_tree(ROOT / "models", package_root / "models")
        copy_tree(ROOT / "quality_knowledge", package_root / "quality_knowledge")

        runtime_root = package_root / "vendor" / "unified_agent_runtime"
        if runtime_root.exists():
            shutil.rmtree(runtime_root)
        with tempfile.TemporaryDirectory(prefix="storage-runtime-pin-") as temp_name:
            temp = Path(temp_name)
            archive = temp / "runtime.tar"
            with archive.open("wb") as stream:
                subprocess.run(
                    ["git", "archive", "--format=tar", RUNTIME_PIN, "runtime",
                     "contracts",
                     "config/runtime/model.yaml", "tools/openai_mock/server.py",
                     "requirements-runtime-p0-test.txt"],
                    cwd=ROOT, check=True, stdout=stream,
                )
            extracted = temp / "runtime"
            extracted.mkdir()
            with zipfile.ZipFile(archive) if archive.suffix == ".zip" else _tar_open(archive) as tar:
                tar.extractall(extracted)
            for rel in ("runtime", "contracts", "config/runtime/model.yaml", "tools/openai_mock/server.py", "requirements-runtime-p0-test.txt"):
                copy_tree(extracted / rel, runtime_root / rel) if (extracted / rel).is_dir() else copy_file(extracted / rel, runtime_root / rel)
        (runtime_root / "RUNTIME_COMMIT").write_text(RUNTIME_PIN + "\n", encoding="utf-8")
        copy_file(ROOT / "config/runtime/agents/knowledge.production.extract.yaml",
                  package_root / "config/runtime/agents/knowledge.production.extract.yaml")
        copy_tree(ROOT / "prompts/runtime/knowledge_production",
                  package_root / "prompts/runtime/knowledge_production")

        actual_pin = (runtime_root / "RUNTIME_COMMIT").read_text(encoding="utf-8").strip()
        if (actual_pin != RUNTIME_PIN
                or not (runtime_root / "runtime/__init__.py").is_file()
                or not (runtime_root / "contracts/__init__.py").is_file()):
            raise SystemExit("RUNTIME_PIN_CLOSURE_FAILED")
        if not (package_root / "config/knowledge_service.local.json").exists():
            pass
        if any(package_root.rglob("knowledge_service.local.json")):
            raise SystemExit("LOCAL_KNOWLEDGE_URL_MUST_NOT_BE_PACKAGED")

        manifest = {
            "package_id": package_id,
            "package_type": "PRODUCT_TEST_CANDIDATE",
            "product": "Storage RC1",
            "source_commit": source_commit,
            "base_head": "cf3b1b97a274bc9b93224e4eaf44482073c4e90c",
            "last_product_source_baseline": "fc52f990aed6fd61501799742ce7ea1d057b5aa3",
            "public_knowledge_source_baseline": "a9536f3814f9e3cd2b9e040e216f68ea61a07c15",
            "runtime_expected_commit": RUNTIME_PIN,
            "runtime_provenance_source": "RUNTIME_COMMIT",
            "single_url_config": "KNOWLEDGE_SERVICE_URL",
            "user_config_count": 1,
            "knowledge_consumer_gateway": "READ_ONLY_PROXY_TO_EXISTING_SERVICE",
            "provider_calls": 0,
            "kioxia_extraction": "NOT_RERUN",
            "product_gate_pass": "NOT_CLAIMED",
            "rc_pass": "NOT_CLAIMED",
            "built_at": datetime.now(timezone.utc).isoformat(),
        }
        (package_root / "CANDIDATE_MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        write_file_hashes(package_root)

        zip_path = output_dir / zip_name
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(package_root.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(work).as_posix())
        digest = sha256(zip_path)
        sha_file = output_dir / f"{zip_name}.sha256"
        sha_file.write_text(f"{digest}  {zip_name}\n", encoding="utf-8")
        delivery = output_dir / f"{zip_name}.DELIVERY_MANIFEST.json"
        delivery.write_text(json.dumps({**manifest, "package": zip_name,
                                        "package_sha256": digest,
                                        "package_size": zip_path.stat().st_size,
                                        "fresh_extract_required": True},
                                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return zip_path, sha_file, delivery
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _tar_open(path: Path):
    import tarfile
    return tarfile.open(path, "r:")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--build-date")
    args = parser.parse_args()
    package, sha_file, delivery = build(args.output_dir.resolve(), args.build_date)
    print(f"PACKAGE={package}")
    print(f"SHA256={sha_file.read_text(encoding='utf-8').split()[0]}")
    print(f"SHA_FILE={sha_file}")
    print(f"DELIVERY_MANIFEST={delivery}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
