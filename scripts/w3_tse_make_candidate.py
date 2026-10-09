"""Reproducible internal test package from W3 frozen product commit."""
import hashlib, importlib, json, os, sys, zipfile
from pathlib import Path

SHA = "c7935e00f1f0abe828b0090524bb14ba923f3635"
NAME = "HARDWARE_W3_INTERNAL_TEST_CANDIDATE_c7935e0"
product, out = (Path(v).resolve() for v in sys.argv[1:3])
sys.path.insert(0, str(product))
b = importlib.import_module("scripts.build_hardware_r1_e2e_validation_package")
assert b.ROOT == product and os.environ["GITHUB_SHA"] == SHA
b.PACKAGE = NAME
sys.argv = ["builder","--output-dir",str(out)]
b.main()
target = out / (NAME + ".zip")
prefix = NAME + "/"
mac = """#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"
if ! python3.11 -c 'import sys; assert sys.version_info[:2] == (3,11)'; then
  echo "BLOCKED: Python 3.11 required" >&2; exit 3
fi
export HARDWARE_CASE_MODEL_CONFIG="$ROOT/config/runtime/hardware_w3_mock_only.example.yaml"
export HARDWARE_R1_E2E_KNOWLEDGE_ENV=NON_PROD
export HARDWARE_R1_E2E_KNOWLEDGE_MODE=LOCAL_NON_PROD
export HARDWARE_R1_E2E_DATA_ROOT="$HOME/Library/Application Support/HardwareW3Candidate_c7935e0/Data"
export HARDWARE_R1_E2E_BOOTSTRAP_PATH="$HOME/Library/Application Support/HardwareW3Candidate_c7935e0/bootstrap.json"
exec python3.11 scripts/hardware_r1_e2e_validation_start.py --host 127.0.0.1 --port 18782 --no-browser
"""
win = r"""@echo off
setlocal
cd /d "%~dp0"
py -3.11 -c "import sys; assert sys.version_info[:2] == (3,11)"
if errorlevel 1 (
  echo BLOCKED: Python 3.11 required
  exit /b 3
)
set "HARDWARE_CASE_MODEL_CONFIG=%~dp0config\runtime\hardware_w3_mock_only.example.yaml"
set "HARDWARE_R1_E2E_KNOWLEDGE_MODE=LOCAL_NON_PROD"
set "HARDWARE_R1_E2E_KNOWLEDGE_ENV=NON_PROD"
set "HARDWARE_R1_E2E_DATA_ROOT=%LOCALAPPDATA%\HardwareW3Candidate_c7935e0\Data"
set "HARDWARE_R1_E2E_BOOTSTRAP_PATH=%LOCALAPPDATA%\HardwareW3Candidate_c7935e0\bootstrap.json"
py -3.11 scripts\hardware_r1_e2e_validation_start.py --host 127.0.0.1 --port 18782 --no-browser
endlocal
"""
model = """active_model: w3_mock_local
models:
  w3_mock_local:
    provider: openai_compatible
    base_url: http://127.0.0.1:18783/v1
    api_key: W3_TEST_DUMMY_TOKEN_ONLY
    model: mock-gpt
    temperature: 0
    max_tokens: 4096
"""
readme = """W3 INTERNAL TEST CANDIDATE ONLY, NOT FORMAL RELEASE
SOURCE_SHA=c7935e00f1f0abe828b0090524bb14ba923f3635
Python 3.11 required; install requirements.txt and requirements-runtime-p0-test.txt.
Windows: START_HARDWARE_W3_MOCK_TEST.bat
Mac: START_HARDWARE_W3_MOCK_TEST.command
Browser: http://127.0.0.1:18782/p0/hardware-cases/knowledge-production
Local Mock must be separately configured on 127.0.0.1:18783. Stage A/B
Fixtures belong to TSE. Model profile has a dummy key, no real Provider.
Independent OS-specific app data. Do not touch 8080/18781 or publish.
This reuses the W2 R1 E2E packager's dependency closure: historical
FOUR_DOC_REAL_E2E scope in original manifest is NOT a W3 Gate result.
G3/G5-G8, Windows/macOS fresh extract and Real Provider NOT_RUN.
"""
extras = {
    "START_HARDWARE_W3_MOCK_TEST.command":mac.encode(),
    "START_HARDWARE_W3_MOCK_TEST.bat":win.replace("\n","\r\n").encode(),
    "config/runtime/hardware_w3_mock_only.example.yaml":model.encode(),
    "tools/openai_mock/__init__.py":(product/"tools/openai_mock/__init__.py").read_bytes(),
    "tools/openai_mock/server.py":(product/"tools/openai_mock/server.py").read_bytes(),
    "requirements-runtime-p0-test.txt":(product/"requirements-runtime-p0-test.txt").read_bytes(),
    "README_W3_INTERNAL_TEST_CANDIDATE.txt":readme.encode(),
}
manifest={
    "task":"HARDWARE-W3-TEST-FIX-FASTLOOP-001",
    "source_commit":SHA,
    "release_status":"INTERNAL_TEST_ONLY_NOT_FORMAL",
    "code_modified":False,
    "mock_only":True,
    "web_host":"127.0.0.1","web_port":18782,"mock_port":18783,
    "windows_entrypoint":"START_HARDWARE_W3_MOCK_TEST.bat",
    "mac_entrypoint":"START_HARDWARE_W3_MOCK_TEST.command",
    "T1":"20/20 TSE reported",
    "T2_G4":"TSE reported pass",
    "T2_G3_G5_G6_G7_G8":"NOT_RUN",
    "win_mac_fresh_extract":"NOT_RUN",
    "real_provider":"NOT_RUN",
    "existing_builder_scope":"FOUR_DOC_REAL_E2E - legacy; NOT W3 release proof",
    "added_files":[{"path":k,"sha256":hashlib.sha256(v).hexdigest()} for k,v in sorted(extras.items())]
}
extras["W3_TEST_CANDIDATE_MANIFEST.json"]=(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n").encode()
with zipfile.ZipFile(target,"a",compression=zipfile.ZIP_STORED) as z:
    current=set(z.namelist())
    for name,data in sorted(extras.items()):
        full=prefix+name
        assert full not in current
        info=zipfile.ZipInfo(full,date_time=(2020,1,1,0,0,0))
        info.compress_type=zipfile.ZIP_STORED
        info.create_system=3
        info.external_attr=((0o100755 if name.endswith(".command") else 0o100644)<<16)
        z.writestr(info,data)
with zipfile.ZipFile(target) as z:
    assert z.testzip() is None
    names=set(z.namelist())
    for path in (
      "services/hardware_case_r1_workbench.py",
      "services/hardware_r1_batch_concurrency.py",
      "services/hardware_w3_capacity_gate.py",
      "quality_knowledge/web/hardware_r1_workbench_api.py",
      "quality_knowledge/web/static/hardware_case_knowledge_production.js",
      "quality_knowledge/web/templates/hardware_case_knowledge_production.html",
      "START_HARDWARE_W3_MOCK_TEST.command",
      "START_HARDWARE_W3_MOCK_TEST.bat",
      "config/runtime/hardware_w3_mock_only.example.yaml",
      "W3_TEST_CANDIDATE_MANIFEST.json",
    ):
        assert prefix+path in names, path
print("W3_ZIP="+str(target))
sha=hashlib.sha256(target.read_bytes()).hexdigest()
print("W3_SHA256="+sha)
print("W3_ENTRIES="+str(len(names)))
(out/(NAME+"_SHA256.txt")).write_text(
    "SOURCE_SHA="+SHA+"\nZIP_SHA256="+sha+"\nFILE="+target.name+"\nFORMAL_RELEASE=NO\n")
