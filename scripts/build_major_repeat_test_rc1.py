"""Build a source based, synthetic-data-only Windows UAT test package."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "MAJOR_REPEAT_PRODUCT_MVP_TEST_RC1"
CODE_DIRS = (
    "builder", "common", "compatibility", "contracts", "models", "parser",
    "parsing", "presentation", "quality_knowledge", "repositories", "retriever",
    "runtime", "services", "schema",
)
CODE_SUFFIXES = {".py", ".sql", ".html", ".css", ".js", ".json", ".yaml", ".yml", ".txt"}
TESTS = (
    "test_golden_e2e_001.py", "test_repeat_web_mvp.py",
    "test_quality_capability_p0_workbench_ued.py", "test_repeat_itr_subject.py",
    "test_repeat_search_contract.py", "test_repeat_result_contract.py",
    "test_historical_case_consumer_contract.py", "test_case_publish_adapter.py",
    "test_case_publish_service.py",
)
SCREENSHOTS = (
    "H01_query_before.png", "H02_running.png", "H03_success.png",
    "H04_evidence_drawer.png", "H05_decision.png", "H06_empty.png",
    "H07_search_unavailable.png", "H08_incomplete.png", "H09_case_list.png",
    "H10_case_detail.png",
)
DOCS = (
    "USER_TEST_GUIDE.md", "DEPLOYMENT_TEST_GUIDE.md", "TEST_SCOPE.md",
    "KNOWN_LIMITATIONS.md", "TARGET_UAT_CHECKLIST.md",
    "ENGINEERING_GOLDEN_RESULT.md",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(("git", *args), cwd=ROOT, text=True).strip()


def copy(source: Path, target: Path) -> None:
    if not source.is_file():
        raise SystemExit(f"MISSING_REQUIRED_FILE={source.relative_to(ROOT)}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def stage_app(stage: Path) -> None:
    tracked = git("ls-files", "-z").split("\0")
    for raw in tracked:
        if not raw:
            continue
        path = Path(raw)
        if path.parts[0] not in CODE_DIRS:
            continue
        if path.suffix.lower() not in CODE_SUFFIXES and not (
            path.parts[:2] == ("quality_knowledge", "prompts_v2") and path.suffix.lower() == ".md"
        ):
            continue
        if any(part in {"__pycache__", "data", "output", "outputs"} for part in path.parts):
            continue
        if path.name.lower() in {"model.local.yaml", "model.local.yml", ".env"}:
            continue
        if path.parts[0] == "runtime" and "test" in path.parts:
            continue
        copy(ROOT / path, stage / "app" / path)

    for relative in ("requirements.txt", "scripts/major_repeat_test_rc1.py"):
        copy(ROOT / relative, stage / "app" / relative)
    for name in TESTS:
        copy(ROOT / "tests" / name, stage / "app/tests" / name)
    for relative in ("config/app.yaml", "config/retrieval.yaml"):
        copy(ROOT / relative, stage / "app" / relative)

    # This package has no real provider endpoint or credential. Repeat search
    # uses the existing local_hash provider in the original model config.
    model = (ROOT / "config/model.yaml").read_text(encoding="utf-8")
    model = model.replace("api_key_env: acca", "api_key_env: QUALITY_AI_API_KEY")
    (stage / "app/config/model.yaml").write_text(model, encoding="utf-8")
    runtime_config = stage / "app/config/runtime"
    runtime_config.mkdir(parents=True, exist_ok=True)
    (runtime_config / "model.yaml").write_text(
        "active_model: qwen_prod\nmodels:\n  qwen_prod:\n"
        "    provider: openai_compatible\n"
        "    base_url_env: QUALITY_AI_BASE_URL\n"
        "    api_key_env: QUALITY_AI_API_KEY\n"
        "    model: __SET_IN_TARGET_ENV__\n"
        "    temperature: 0\n    max_tokens: 8192\n",
        encoding="utf-8",
    )
    (stage / "config").mkdir(parents=True, exist_ok=True)
    (stage / "config/model.local.example.yaml").write_text(
        "active_model: target_model\nmodels:\n  target_model:\n"
        "    provider: openai_compatible\n"
        "    base_url_env: QUALITY_AI_BASE_URL\n"
        "    api_key_env: QUALITY_AI_API_KEY\n"
        "    model: __SET_IN_TARGET_ENV__\n",
        encoding="utf-8",
    )
    (stage / "config/README.md").write_text(
        "Provider 配置仅用于目标环境需要 AI 分析时。请在目标环境通过环境变量 "
        "QUALITY_AI_BASE_URL / QUALITY_AI_API_KEY 配置，勿写入本包。合成演示的 Repeat "
        "检索使用仓库现有 local_hash，不需要 Provider Secret。\n",
        encoding="utf-8",
    )


def scan(stage: Path) -> dict[str, object]:
    files = [path for path in stage.rglob("*") if path.is_file()]
    forbidden_names = {".env", ".env.local", "model.local.yaml", "model.local.yml"}
    forbidden_suffixes = {".db", ".sqlite", ".sqlite3", ".log", ".pyc", ".pyo", ".pem", ".key"}
    personal_path = re.compile(r"/Users/[^/\s]+/|[A-Za-z]:\\Users\\[^\\\s]+\\|/var/folders/|\.codex/")
    literal_secret = re.compile(
        r"(?im)^\s*(?:api_key|password|authorization)\s*[:=]\s*"
        r"(?!__|\$|os\.|None|['\"]?['\"]?\s*$)[^#\r\n]+$"
    )
    token = re.compile(r"(?i)\b(?:Bearer\s+[A-Za-z0-9._~-]{20,}|sk-[A-Za-z0-9]{20,})\b")
    problems = []
    for path in files:
        rel = path.relative_to(stage).as_posix()
        if path.name.lower() in forbidden_names or path.suffix.lower() in forbidden_suffixes:
            problems.append(f"FORBIDDEN_FILE:{rel}")
        if path.suffix.lower() not in CODE_SUFFIXES and path.suffix.lower() not in {".bat", ".md"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if personal_path.search(text):
            problems.append(f"PERSONAL_PATH:{rel}")
        if token.search(text):
            problems.append(f"TOKEN_LITERAL:{rel}")
        if path.suffix.lower() in {".yaml", ".yml"} and literal_secret.search(text):
            problems.append(f"CONFIG_SECRET_LITERAL:{rel}")
    if problems:
        raise SystemExit("SECRET_SCAN_FAIL=" + ",".join(problems))
    return {"result": "PASS", "files_scanned": len(files), "forbidden_findings": 0}


def build(out_dir: Path, screenshots: Path) -> tuple[Path, str]:
    source_commit = git("rev-parse", "HEAD")
    main_commit = git("rev-parse", "origin/main")
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="major-repeat-rc1-build-") as directory:
        stage = Path(directory) / NAME
        stage.mkdir()
        stage_app(stage)
        for filename in DOCS:
            copy(ROOT / "docs/product/major_repeat_test_rc1" / filename, stage / "docs" / filename)
        for filename in SCREENSHOTS:
            source = screenshots / filename
            if not source.is_file():
                raise SystemExit(f"MISSING_H01_H10_SCREENSHOT={filename}")
            copy(source, stage / "evidence/repeat_web_h01_h10" / filename)
        copy(ROOT / "packaging/major_repeat_test_rc1/run_windows.bat", stage / "run_windows.bat")
        copy(ROOT / "packaging/major_repeat_test_rc1/smoke_test.bat", stage / "scripts/smoke_test.bat")
        copy(ROOT / "packaging/major_repeat_test_rc1/golden_test.bat", stage / "scripts/golden_test.bat")
        (stage / "VERSION").write_text("MAJOR_REPEAT_PRODUCT_MVP_TEST_RC1\n", encoding="utf-8")

        security = scan(stage)
        (stage / "evidence/security_scan.json").write_text(
            json.dumps(security, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        files = [
            {"path": p.relative_to(stage).as_posix(), "sha256": sha256(p), "size": p.stat().st_size}
            for p in sorted(stage.rglob("*")) if p.is_file()
        ]
        manifest = {
            "package": NAME,
            "package_type": "TEST_RC",
            "product": "MAJOR_ISSUE_CASE_LIBRARY_REPEAT_RISK",
            "product_stage": "MVP_INTEGRATION",
            "test_package": "READY",
            "engineering_golden": "PASS",
            "target_env": "PENDING",
            "mvp_ready": False,
            "source_main": main_commit,
            "source_commit": source_commit,
            "golden_e2e_source": "4180e4ee1c2cb78e6b691f457b8a79a64ccb3cd1",
            "build_time": datetime.now(timezone.utc).isoformat(),
            "historical_case_contract": "historical-case/v1",
            "repeat_result_contract": "repeat-result/v1",
            "runtime_reused": True,
            "knowledge_platform_reused": True,
            "sample_type": "REPOSITORY_SYNTHETIC_FIXTURE",
            "windows_startup": "TARGET_WINDOWS_PENDING",
            "screenshots": {
                "source_commit": "9af9aa37785c34da18b71402b62d5fdcaa44c32e",
                "ci_artifact_id": 10784110722,
            },
            "security_scan": security,
            "files": files,
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        lines = []
        for path in sorted(stage.rglob("*")):
            if path.is_file() and path.name != "SHA256SUMS":
                lines.append(f"{sha256(path)}  {path.relative_to(stage).as_posix()}")
        (stage / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
        scan(stage)

        archive = out_dir / f"{NAME}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    bundle.write(path, f"{NAME}/{path.relative_to(stage).as_posix()}")
        digest = sha256(archive)
        (out_dir / f"{NAME}.zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive, digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--screenshots-dir", type=Path, required=True)
    args = parser.parse_args()
    archive, digest = build(args.out_dir.resolve(), args.screenshots_dir.resolve())
    print(f"PACKAGE={archive}")
    print(f"SHA256={digest}")
    print("SECRET_SCAN=PASS")
    print("TARGET_ENV=PENDING")
    print("MVP_READY=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
