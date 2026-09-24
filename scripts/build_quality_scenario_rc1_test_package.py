from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_BASELINE = "6ad93f1396585163fffd1e4a70a677aee917a6ff"
ENGINEERING_HEAD = "66df062b28527a286033ad7632a7ac79247efe82"
RUNTIME_BASELINE = "0959da43008307398a9cac0f9abfc7fec26dcb8a"
PACKAGE_NAME = "QUALITY_SCENARIO_MVP_RC1_TEST_PACKAGE_20260924"
EXCLUDED_DIRS = {".git", ".github", ".pytest_cache", "__pycache__", ".deps", "knowledge", "input", "output", "baseline_release", "releases", "deliverables"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".log", ".zip", ".xlsx", ".xls", ".xlsm", ".pdf", ".doc", ".docx"}
REQUIRED_PROJECT_DIRS = ("quality_knowledge", "prompts/runtime", "config/runtime/agents")
RC1_DOCS = (
    "docs/QUALITY_SCENARIO_MVP_RC1_RELEASE_NOTE.md",
    "docs/QUALITY_SCENARIO_MVP_RC1_E2E_REPORT.md",
    "docs/QUALITY_SCENARIO_MVP_RC1_KNOWN_LIMITATIONS.md",
)


def copy_tree(src: Path, dst: Path) -> None:
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def write_launchers(pkg: Path) -> None:
    (pkg / "run_windows.bat").write_text(r"""@echo off
setlocal
cd /d "%~dp0"
if not exist "data" mkdir "data"
set "PYTHONPATH=%CD%;%CD%\vendor\unified_agent_runtime;%PYTHONPATH%"
if exist "config\runtime\model.local.yaml" set "REVERSE_QUALITY_MODEL_CONFIG=%CD%\config\runtime\model.local.yaml"
echo Open after startup: http://127.0.0.1:8080/p0/quality-scenarios/workbench
where py >nul 2>nul
if %errorlevel%==0 (
  py tools\run_quality_scenario_rc1.py --db "data\quality_scenario_rc1.db"
) else (
  python tools\run_quality_scenario_rc1.py --db "data\quality_scenario_rc1.db"
)
set EXIT_CODE=%errorlevel%
if not "%QUALITY_SCENARIO_NO_PAUSE%"=="1" pause
endlocal & exit /b %EXIT_CODE%
""", encoding="utf-8")

    (pkg / "install_windows.bat").write_text(r"""@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -m pip install -r requirements.txt
  if errorlevel 1 exit /b %errorlevel%
  py -m pip install -r vendor\unified_agent_runtime\requirements-runtime-p0-test.txt
) else (
  python -m pip install -r requirements.txt
  if errorlevel 1 exit /b %errorlevel%
  python -m pip install -r vendor\unified_agent_runtime\requirements-runtime-p0-test.txt
)
set EXIT_CODE=%errorlevel%
echo Dependencies installed.
pause
endlocal & exit /b %EXIT_CODE%
""", encoding="utf-8")

    (pkg / "verify_windows.bat").write_text(r"""@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%CD%;%CD%\vendor\unified_agent_runtime;%PYTHONPATH%"
where py >nul 2>nul
if %errorlevel%==0 (
  py tools\verify_quality_scenario_rc1_package.py
) else (
  python tools\verify_quality_scenario_rc1_package.py
)
set EXIT_CODE=%errorlevel%
pause
endlocal & exit /b %EXIT_CODE%
""", encoding="utf-8")

    (pkg / "prepare_internal_golden.bat").write_text(r"""@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%CD%;%CD%\vendor\unified_agent_runtime;%PYTHONPATH%"
if exist "config\runtime\model.local.yaml" set "REVERSE_QUALITY_MODEL_CONFIG=%CD%\config\runtime\model.local.yaml"
where py >nul 2>nul
if %errorlevel%==0 (
  py tools\prepare_internal_golden_candidate.py %*
) else (
  python tools\prepare_internal_golden_candidate.py %*
)
set EXIT_CODE=%errorlevel%
endlocal & exit /b %EXIT_CODE%
""", encoding="utf-8")


def write_tools(pkg: Path) -> None:
    tools = pkg / "tools"
    tools.mkdir(parents=True, exist_ok=True)

    (tools / "run_quality_scenario_rc1.py").write_text("""from __future__ import annotations
import argparse
from pathlib import Path
import uvicorn
from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app

ROOT = Path(__file__).resolve().parents[1]

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=str(ROOT / "data/quality_scenario_rc1.db"))
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    args = p.parse_args()
    db = Path(args.db)
    if not db.is_absolute():
        db = (ROOT / db).resolve()
    db.parent.mkdir(parents=True, exist_ok=True)
    init = P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    )
    if db.exists():
        init.verify_ready(db)
    else:
        init.initialize(db)
    uvicorn.run(create_p0_app(db, project_root=ROOT), host=args.host, port=args.port)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
""", encoding="utf-8")

    (tools / "verify_quality_scenario_rc1_package.py").write_text("""from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor/unified_agent_runtime"))
from quality_knowledge.quality_scenario_v1 import ScenarioStatus
from runtime import AgentRequest

m = json.loads((ROOT / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
assert m["product_baseline"] == "6ad93f1396585163fffd1e4a70a677aee917a6ff"
assert m["runtime_baseline"] == "0959da43008307398a9cac0f9abfc7fec26dcb8a"
assert ScenarioStatus.PUBLISHED.value == "PUBLISHED"
assert AgentRequest is not None
for rel in (
    "quality_knowledge/web/templates/p0_quality_scenario_workbench.html",
    "quality_knowledge/web/templates/p0_quality_scenario_library.html",
    "quality_knowledge/web/templates/p0_quality_scenario_detail.html",
    "vendor/unified_agent_runtime/runtime/__init__.py",
):
    assert (ROOT / rel).is_file(), rel
print("QUALITY_SCENARIO_RC1_PACKAGE_VERIFY=PASS")
print("PRODUCT_BASELINE=" + m["product_baseline"])
print("RUNTIME_BASELINE=" + m["runtime_baseline"])
print("FILE_COUNT=" + str(m["file_count"]))
""", encoding="utf-8")

    (tools / "prepare_internal_golden_candidate.py").write_text("""from __future__ import annotations
import argparse
import json
from pathlib import Path
from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.quality_scenario_candidate_v1_service import CandidateV1Service
from quality_knowledge.quality_scenario_v1_store import SQLiteQualityScenarioV1Repository
from quality_knowledge.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]

def main() -> int:
    p = argparse.ArgumentParser(description="Prepare one company-internal Golden Candidate.")
    p.add_argument("--source-db", required=True)
    p.add_argument("--material-id", required=True, type=int)
    p.add_argument("--product-db", default=str(ROOT / "data/quality_scenario_rc1.db"))
    p.add_argument("--product-code", required=True)
    p.add_argument("--trigger-source", required=True, choices=["HIGH_PERCEPTION", "RND_VALUE"])
    p.add_argument("--trigger-reason", required=True)
    p.add_argument("--created-by", default="INTERNAL_GOLDEN")
    p.add_argument("--force-reverse", action="store_true")
    args = p.parse_args()

    source_db = Path(args.source_db).expanduser().resolve()
    if not source_db.is_file():
        raise SystemExit("SOURCE_DB_NOT_FOUND")
    product_db = Path(args.product_db).expanduser()
    if not product_db.is_absolute():
        product_db = (ROOT / product_db).resolve()

    source_app = create_app(source_db)
    reverse_service = source_app.state.reverse_quality_service
    taxonomy = source_app.state.scenario_repository.taxonomy_active(args.product_code)
    if not taxonomy:
        raise SystemExit("ACTIVE_TAXONOMY_NOT_FOUND")
    reverse = reverse_service.analyse(args.material_id, args.product_code, force=args.force_reverse)
    reverse_result = reverse.get("result")
    if not isinstance(reverse_result, dict):
        raise SystemExit("REVERSE_QUALITY_RESULT_NOT_AVAILABLE")

    init = P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    )
    product_db.parent.mkdir(parents=True, exist_ok=True)
    if product_db.exists():
        init.verify_ready(product_db)
    else:
        init.initialize(product_db)

    produced = CandidateV1Service(
        SQLiteQualityScenarioV1Repository(product_db)
    ).create_from_reverse(
        reverse_result,
        taxonomy,
        trigger_source=args.trigger_source,
        trigger_reason=args.trigger_reason,
        created_by=args.created_by,
    )
    out = {
        "result": "CANDIDATE_READY",
        "reverse_run_id": reverse_result.get("run_id", ""),
        "reverse_analysis_id": reverse_result.get("analysis_id", ""),
        "scenario_id": produced.scenario.scenario_id,
        "scenario_version": produced.scenario.scenario_version,
        "status": produced.scenario.status.value,
        "created": produced.created,
        "blockers": produced.scenario.blockers,
        "missing_information_count": len(produced.scenario.missing_information),
        "next": "Open P01 Workbench and perform real human Review / Confirm / Publish.",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
""", encoding="utf-8")


def write_model_example(pkg: Path) -> None:
    p = pkg / "config/runtime/model.local.example.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("""# Copy to model.local.yaml only when the default validated Runtime profile
# does not match the company test environment. Never place a real API key here.
active_model: qwen_prod
models:
  qwen_prod:
    provider: openai_compatible
    base_url_env: QS_PROVIDER_BASE_URL
    api_key_env: QS_PROVIDER_API_KEY
    model: __REPLACE_WITH_COMPANY_MODEL__
    temperature: 0
    max_tokens: 8192
""", encoding="utf-8")


def write_readme(pkg: Path) -> None:
    (pkg / "README_TEST_PACKAGE.md").write_text("""# 质量场景库 MVP RC1 提测包

状态：ENGINEERING_RC1_PASS / INTERNAL_GOLDEN_PENDING

## 基线
Product baseline: 6ad93f1396585163fffd1e4a70a677aee917a6ff
Engineering head: 66df062b28527a286033ad7632a7ac79247efe82
Unified Runtime baseline: 0959da43008307398a9cac0f9abfc7fec26dcb8a
Engineering Gate: 147 passed / 0 failed

## Windows 安装与启动
1. 解压到全新目录，不覆盖生产目录。
2. 运行 install_windows.bat。
3. 如公司Provider与默认profile不同，把 config/runtime/model.local.example.yaml 复制为 model.local.yaml，并使用环境变量放API Key。
4. 运行 verify_windows.bat，必须看到 QUALITY_SCENARIO_RC1_PACKAGE_VERIFY=PASS。
5. 运行 run_windows.bat。
6. 打开 http://127.0.0.1:8080/p0/quality-scenarios/workbench 。

默认独立测试库：data/quality_scenario_rc1.db，不覆盖生产SQLite。

## 公司环境真实Golden
真实材料不得上传GitHub或外部服务。先配置公司允许的Real Provider，然后执行：

prepare_internal_golden.bat --source-db "D:\\path\\internal_quality_issue.db" --material-id 123 --product-code PLC --trigger-source HIGH_PERCEPTION --trigger-reason "真实业务触发原因"

该命令只执行：
内部问题事实 → Reverse Quality → Unified Runtime / Real Provider → ReverseQualityResult V0.1 → ScenarioCandidateV1

它不会替代人工Review、专业质量确认、研发技术确认或Publish。
命令返回scenario_id后，在P01中人工完成Review / Confirm / Publish，再到P02/P03检查正式资产与Evidence。

## 安全
不复制真实Excel/PDF/Word/SQLite到外传包；不记录真实API Key/Authorization；不手工改库绕过Gate。
真实Golden通过前，状态只能是 ENGINEERING_RC1_PASS / INTERNAL_GOLDEN_PENDING。
""", encoding="utf-8")


def make_manifest(pkg: Path, source_commit: str) -> dict:
    files = []
    for path in sorted(pkg.rglob("*")):
        if not path.is_file() or path.name == "PACKAGE_MANIFEST.json":
            continue
        files.append({
            "path": path.relative_to(pkg).as_posix(),
            "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    return {
        "package_name": PACKAGE_NAME,
        "status": "ENGINEERING_RC1_PASS / INTERNAL_GOLDEN_PENDING",
        "product_baseline": PRODUCT_BASELINE,
        "engineering_head": ENGINEERING_HEAD,
        "runtime_baseline": RUNTIME_BASELINE,
        "package_source_commit": source_commit,
        "file_count": len(files),
        "files": files,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--runtime-root", required=True)
    p.add_argument("--output-root", default="dist")
    args = p.parse_args()

    runtime_root = Path(args.runtime_root).resolve()
    out = (ROOT / args.output_root).resolve()
    pkg = out / PACKAGE_NAME
    if pkg.exists():
        shutil.rmtree(pkg)
    pkg.mkdir(parents=True, exist_ok=True)

    for rel in REQUIRED_PROJECT_DIRS:
        src = ROOT / rel
        if not src.exists():
            raise SystemExit("REQUIRED_PATH_MISSING:" + rel)
        copy_tree(src, pkg / rel)

    shutil.copy2(ROOT / "requirements.txt", pkg / "requirements.txt")
    for rel in RC1_DOCS:
        src = ROOT / rel
        if not src.is_file():
            raise SystemExit("REQUIRED_DOC_MISSING:" + rel)
        dst = pkg / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    src = ROOT / "tests/golden/quality_scenario_rc1_golden_v01.json"
    dst = pkg / "tests/golden/quality_scenario_rc1_golden_v01.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)

    copy_tree(runtime_root / "runtime", pkg / "vendor/unified_agent_runtime/runtime")
    shutil.copy2(runtime_root / "requirements-runtime-p0-test.txt", pkg / "vendor/unified_agent_runtime/requirements-runtime-p0-test.txt")
    runtime_model = runtime_root / "config/runtime/model.yaml"
    if not runtime_model.is_file():
        raise SystemExit("RUNTIME_MODEL_CONFIG_MISSING")
    dst_model = pkg / "config/runtime/model.yaml"
    dst_model.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(runtime_model, dst_model)

    write_launchers(pkg)
    write_tools(pkg)
    write_model_example(pkg)
    write_readme(pkg)

    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    manifest = make_manifest(pkg, commit)
    (pkg / "PACKAGE_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(pkg)
    print("files=" + str(manifest["file_count"]))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
