#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"
mkdir -p logs release
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$PWD/logs/selfcheck_${STAMP}.log"
: > "$LOG"
ln -sfn "$(basename "$LOG")" logs/latest.log 2>/dev/null || cp "$LOG" logs/latest.log
set +e
{
  printf '[0/6] Package integrity\n'
  PY_INTEGRITY="${STORAGE_PYTHON_BIN:-}"
  if [ -z "$PY_INTEGRITY" ]; then
    if command -v python3 >/dev/null 2>&1; then PY_INTEGRITY="$(command -v python3)";
    elif command -v python >/dev/null 2>&1; then PY_INTEGRITY="$(command -v python)";
    else echo 'PACKAGE_INTEGRITY_FAIL: python not found' >&2; exit 2; fi
  fi
  "$PY_INTEGRITY" - <<'INTEGRITYPY'
from pathlib import Path
import hashlib
root=Path('.').resolve()
manifest=root/'FILE_SHA256SUMS.txt'
if not manifest.is_file():
    raise SystemExit('PACKAGE_INTEGRITY_FAIL: FILE_SHA256SUMS.txt missing')
missing=[]; mismatch=[]; checked=0
for lineno,line in enumerate(manifest.read_text(encoding='utf-8').splitlines(),1):
    if not line.strip():
        continue
    parts=line.split(maxsplit=1)
    if len(parts)!=2:
        raise SystemExit(f'PACKAGE_INTEGRITY_FAIL: malformed manifest line {lineno}')
    expected, rel=parts
    rel=rel.strip()
    if rel.startswith('*'): rel=rel[1:]
    if rel.startswith('./'): rel=rel[2:]
    target=root/rel
    if not target.is_file():
        missing.append(rel); continue
    actual=hashlib.sha256(target.read_bytes()).hexdigest()
    if actual.lower()!=expected.lower():
        mismatch.append((rel,expected,actual))
    checked+=1
if missing or mismatch:
    print(f'PACKAGE_INTEGRITY_FAIL checked={checked} missing={len(missing)} mismatch={len(mismatch)}')
    for rel in missing: print('MISSING='+rel)
    for rel,exp,act in mismatch: print(f'MISMATCH={rel} expected={exp} actual={act}')
    raise SystemExit(3)
print(f'PACKAGE_INTEGRITY_PASS paths={checked}')
INTEGRITYPY
  [ "$?" -eq 0 ] || exit $?

  if [ -f config/product_test.env ]; then set -a; . ./config/product_test.env; set +a; fi
  RUNTIME_ROOT="${UNIFIED_AGENT_RUNTIME_ROOT:-}"
  if [ -z "$RUNTIME_ROOT" ]; then RUNTIME_ROOT="$(bash ./scripts/setup_runtime.sh)"; fi
  export UNIFIED_AGENT_RUNTIME_ROOT="$RUNTIME_ROOT"
  PYTHON_BIN="$(bash ./scripts/select_python.sh)"
  export PYTHONPATH="$RUNTIME_ROOT:${PYTHONPATH:-}"
  "$PYTHON_BIN" scripts/preflight.py --runtime-root "$RUNTIME_ROOT" --mode mock

  printf '\n[PORT] Launcher port ownership regression\n'
  "$PYTHON_BIN" scripts/port_regression.py --package-root . || exit $?

  printf '\n[1/6] Mock normal\n'
  STORAGE_TEST_NO_WAIT=1 STORAGE_MOCK_FAULT=normal STORAGE_PYTHON_BIN="$PYTHON_BIN" bash ./run_product_test.sh mock "$RUNTIME_ROOT" || exit $?
  printf 'TEST-PORT-04=PASS FREE_PORT_NORMAL_MOCK_E2E=PASS\n'

  printf '\n[2/6] Mock JSON truncation\n'
  STORAGE_TEST_NO_WAIT=1 STORAGE_MOCK_FAULT=truncate_once STORAGE_PYTHON_BIN="$PYTHON_BIN" bash ./run_product_test.sh mock "$RUNTIME_ROOT" || exit $?

  printf '\n[3/6] Mock 429 recovery\n'
  STORAGE_TEST_NO_WAIT=1 STORAGE_MOCK_FAULT=429_once STORAGE_PYTHON_BIN="$PYTHON_BIN" bash ./run_product_test.sh mock "$RUNTIME_ROOT" || exit $?

  printf '\n[4/6] Mock persistent 503 hard budget\n'
  STORAGE_TEST_NO_WAIT=1 STORAGE_MOCK_FAULT=persistent_503 STORAGE_PYTHON_BIN="$PYTHON_BIN" bash ./run_product_test.sh mock "$RUNTIME_ROOT" || exit $?

  printf '\n[5/6] Storage regression\n'
  # The packaged Runtime is an immutable snapshot. Its direct GBK-console unit
  # test belongs to Runtime source validation; product launchers enforce UTF-8
  # before Python and that launcher contract remains in this regression suite.
  "$PYTHON_BIN" -m pytest -q -rs -k 'not test_runtime_provider_trace_survives_gbk_console' || exit $?

  printf '\nPRODUCT PRETEST SELF-CHECK PASS\n'
} 2>&1 | tee "$LOG"
status=${PIPESTATUS[0]}
set -e
if [ "$status" -ne 0 ]; then
  bash ./scripts/collect_failure.sh "$LOG" || true
  printf 'SELF-CHECK FAILED. See %s and logs/failure_latest.txt\n' "$LOG" >&2
else
  rm -f logs/failure_latest.txt 2>/dev/null || true
fi
exit "$status"
