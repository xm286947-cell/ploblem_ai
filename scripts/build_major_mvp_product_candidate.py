"""Build a data-free, manifest-backed Major MVP Product Candidate ZIP."""
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
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
SOURCE_BASE = "55aaf9234ec102360043f4deef782126bc234b80"
PACKAGE_PREFIX = "MAJOR_V11_LINUX_FULL_REAL_E2E_CANDIDATE_"
SOURCE_DIRS = {
    "analysis",
    "builder",
    "common",
    "compatibility",
    "config",
    "contracts",
    "knowledge_production",
    "models",
    "parser",
    "parsing",
    "presentation",
    "prompts",
    "quality_knowledge",
    "repositories",
    "retriever",
    "runtime",
    "schema",
    "services",
}
ROOT_FILES = {
    "main.py",
    "requirements.txt",
    "requirements-runtime-p0-test.txt",
    "requirements-major-mvp-product.txt",
    "MAJOR_MVP_PRODUCT_CANDIDATE_README.md",
}
GENERATED_FILES = {
    "scripts/major_mvp_product_start.py",
    "START_MAJOR_MVP.bat",
    "START_MAJOR_MVP.command",
    "START_MAJOR_MVP.sh",
}
SKIP_DIRS = {
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "artifacts",
    "fixtures",
    "input",
    "knowledge",
    "output",
    "outputs",
    "samples",
    "storage_e2e",
    "test_assets",
    "test_data",
    "testdata",
    "tests",
}
SKIP_SUFFIXES = {
    ".db",
    ".doc",
    ".docx",
    ".env",
    ".log",
    ".p12",
    ".pfx",
    ".pem",
    ".pkl",
    ".pickle",
    ".sqlite",
    ".sqlite3",
    ".xls",
    ".xlsx",
    ".xlsm",
    ".pdf",
    ".key",
}
ABSOLUTE_LOCAL_PATH = re.compile(
    r"(?:/Users/[^\s\"'<>]+|/home/[^\s\"'<>]+|/private/(?:tmp|var)/[^\s\"'<>]+|/tmp/[^\s\"'<>]+|[A-Za-z]:\\(?:Users|Temp|Windows\\Temp)\\[^\s\"'<>]+)"
)
SAFE_LEGACY_MODEL = """# Product-safe default. Configure the endpoint and enable AI after installation.
ai:
  enabled: false
  provider: openai_compatible
  base_url: https://api.openai.com/v1
  api_key_env: MAJOR_MVP_AI_API_KEY
  model: configure-me
  temperature: 0
  max_tokens: 4096
  timeout_seconds: 120
  max_retries: 2
quality_issue_agents: {}
embedding:
  enabled: false
  provider: local_hash
  model: local-hash-v1
  dimensions: 256
"""
SAFE_RUNTIME_MODEL = """# Product-safe Unified Runtime profile; credentials are environment-only.
active_model: major_mvp
models:
  major_mvp:
    provider: openai_compatible
    base_url_env: MAJOR_MVP_AI_BASE_URL
    api_key_env: MAJOR_MVP_AI_API_KEY
    model: configure-me
    temperature: 0
    max_tokens: 8192
"""


class CandidateBuildError(RuntimeError):
    pass


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def source_files(root: Path) -> list[Path]:
    output = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    selected: list[Path] = []
    for raw in output.splitlines():
        rel = PurePosixPath(raw)
        if not raw or rel.is_absolute() or ".." in rel.parts:
            continue
        if rel.parts[0] not in SOURCE_DIRS and raw not in ROOT_FILES | GENERATED_FILES:
            continue
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if rel.suffix.lower() in SKIP_SUFFIXES:
            continue
        lower_name = rel.name.lower()
        if (
            lower_name == ".ds_store"
            or lower_name == ".env"
            or lower_name.startswith(".env.")
            or ".local." in lower_name
            or lower_name.endswith(".local.yaml")
            or lower_name.endswith(".local.yml")
            or lower_name.endswith(".local.json")
            or lower_name.startswith("test_")
            or lower_name.startswith("mock_")
        ):
            continue
        path = root.joinpath(*rel.parts)
        if path.is_file() and not path.is_symlink():
            selected.append(path)
    return sorted(selected, key=lambda item: item.relative_to(root).as_posix())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_text_file(path: Path) -> bool:
    return path.suffix.lower() in {
        ".bat", ".cfg", ".css", ".html", ".ini", ".js", ".json", ".md",
        ".py", ".sh", ".sql", ".svg", ".toml", ".txt", ".xml", ".yaml", ".yml",
    }


def _validate_text_payload(stage: Path) -> None:
    for item in stage.rglob("*"):
        if not item.is_file() or not _is_text_file(item):
            continue
        try:
            text = item.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise CandidateBuildError(f"NON_UTF8_TEXT_FILE={item.relative_to(stage)}") from error
        if ABSOLUTE_LOCAL_PATH.search(text):
            raise CandidateBuildError(f"LOCAL_ABSOLUTE_PATH={item.relative_to(stage)}")
        if item.suffix.lower() in {".yaml", ".yml", ".json", ".toml", ".ini", ".env"}:
            if re.search(
                r"(?im)^\s*(?:api[-_]?key|secret|client[-_]?secret|password|access[-_]?token|refresh[-_]?token)\s*:",
                text,
            ):
                raise CandidateBuildError(f"LITERAL_SECRET_FIELD={item.relative_to(stage)}")


def _write_text(path: Path, value: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _copy_source(root: Path, stage: Path) -> None:
    for source in source_files(root):
        relative = source.relative_to(root).as_posix()
        if relative in {"config/model.yaml", "config/runtime/model.yaml"}:
            continue
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    # Do not distribute development model profiles, local endpoints, or credentials.
    _write_text(stage / "config/model.yaml", SAFE_LEGACY_MODEL)
    _write_text(stage / "config/runtime/model.yaml", SAFE_RUNTIME_MODEL)

    for relative in (
        "data/quality/.keep",
        "data/major/.keep",
        "data/major/attachments/.keep",
        "data/historical_case/.keep",
        "data/runtime/.keep",
        "data/repeat_reports/.keep",
        "data/logs/.keep",
    ):
        _write_text(stage / relative, "Candidate-owned runtime data directory.\n")


def _git_metadata(root: Path, *, require_clean: bool) -> tuple[str, str, str]:
    commit = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "branch", "--show-current")
    if not branch:
        branch = os.getenv("GITHUB_REF_NAME", "").strip() or "DETACHED"
    _git(root, "merge-base", "--is-ancestor", SOURCE_BASE, "HEAD")
    dirty = bool(_git(root, "status", "--porcelain", "--untracked-files=all"))
    if require_clean and dirty:
        raise CandidateBuildError("SOURCE_WORKTREE_DIRTY")
    return commit, branch, "YES" if dirty else "NO"


def _manifest(stage: Path, *, commit: str, branch: str, dirty: str) -> dict[str, object]:
    files = sorted(
        item.relative_to(stage).as_posix()
        for item in stage.rglob("*")
        if item.is_file()
    )
    return {
        "package_id": f"{PACKAGE_PREFIX}{commit[:12]}",
        "product": "MAJOR_CASE_KNOWLEDGE_BASE_X_REPEAT_RISK",
        "source_commit": commit,
        "source_branch": branch,
        "source_base": SOURCE_BASE,
        "source_dirty": dirty == "YES",
        "build_time": datetime.now(UTC).isoformat(timespec="seconds"),
        "contract_versions": {
            "historical_case": "historical-case/v1",
            "repeat_risk_context": "historical-case/repeat-risk-context/v1",
            "semantic_publish_projection": "major-semantic-publish/v1",
            "major_excel_template": "MAJOR_CASE_IMPORT_TEMPLATE_V1.0",
        },
        "capabilities": [
            "I1_EXCEL_ENTRY",
            "I2_TYPED_REVIEW",
            "I3_SEMANTIC_PUBLISH",
            "HISTORICAL_CASE",
            "I4_REPEAT_RISK_TYPED_CONSUMPTION",
            "REPEAT_AGENT_SIMILARITY_M8_2",
            "REPEAT_AGENT_SOLUTION_M8_3",
            "REPEAT_AI_RECOMMENDATION_M8_4_OPTIONAL",
            "REPEAT_MARKDOWN_REPORT",
        ],
        "entrypoints": {
            "major_production": "/p0/major-production",
            "historical_cases": "/p0/cases",
            "repeat_risk": "/p0/issues",
        },
        "launchers": {
            "windows": "START_MAJOR_MVP.bat",
            "macos": "START_MAJOR_MVP.command",
            "posix": "START_MAJOR_MVP.sh",
        },
        "data_root": {
            "default": "data/",
            "layout": [
                "data/quality/db/",
                "data/major/db/",
                "data/major/attachments/",
                "data/historical_case/",
                "data/runtime/",
                "data/repeat_reports/",
                "data/logs/",
            ],
        },
        "excluded": [
            "real_business_data",
            "secrets_and_local_environment_files",
            "local_databases",
            "product_test_results",
            "pytest_caches_and_test_fixtures",
        ],
        "files": files,
        "file_sha256": {relative: _sha256(stage / relative) for relative in files},
        "manifest_hash_note": "PACKAGE_MANIFEST.json is self-excluded from files and file_sha256.",
    }


def _zip_tree(stage: Path, archive: Path, package_id: str) -> None:
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for item in sorted(stage.rglob("*")):
            if not item.is_file():
                continue
            relative = item.relative_to(stage).as_posix()
            archive_name = f"{package_id}/{relative}"
            info = zipfile.ZipInfo.from_file(item, archive_name)
            info.compress_type = zipfile.ZIP_DEFLATED
            with item.open("rb") as stream:
                zf.writestr(info, stream.read(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def build_candidate(
    *,
    source_root: Path = ROOT,
    output_dir: Path | None = None,
    require_clean: bool = True,
) -> Path:
    root = Path(source_root).resolve()
    destination = Path(output_dir or Path(tempfile.gettempdir()) / "major-mvp-product-candidates").resolve()
    commit, branch, dirty = _git_metadata(root, require_clean=require_clean)
    selected = source_files(root)
    required = ROOT_FILES | GENERATED_FILES
    missing = sorted(path for path in required if not (root / path).is_file())
    if missing:
        raise CandidateBuildError("MISSING_CANDIDATE_INPUT=" + ",".join(missing))
    present = {path.relative_to(root).as_posix() for path in selected}
    missing_selected = sorted(required - present)
    if missing_selected:
        raise CandidateBuildError("CANDIDATE_INPUT_FILTERED=" + ",".join(missing_selected))

    package_id = f"{PACKAGE_PREFIX}{commit[:12]}"
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"{package_id}.zip"
    with tempfile.TemporaryDirectory(prefix="major-mvp-package-stage-") as temp:
        stage = Path(temp) / package_id
        stage.mkdir()
        _copy_source(root, stage)
        _validate_text_payload(stage)
        manifest = _manifest(stage, commit=commit, branch=branch, dirty=dirty)
        (stage / "PACKAGE_MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary_archive = Path(temp) / archive.name
        _zip_tree(stage, temporary_archive, package_id)
        os.replace(temporary_archive, archive)

    digest = _sha256(archive)
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return archive


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "major-mvp-product-candidates",
    )
    args = parser.parse_args(argv)
    try:
        archive = build_candidate(output_dir=args.output_dir, require_clean=True)
    except (CandidateBuildError, subprocess.CalledProcessError) as error:
        print(f"PACKAGE_BUILD=FAIL\nERROR={error}")
        return 2
    digest = _sha256(archive)
    print("PACKAGE_BUILD=PASS")
    print(f"PACKAGE={archive}")
    print(f"PACKAGE_SHA256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
