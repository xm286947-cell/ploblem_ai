#!/bin/sh
set -eu
EXPECTED="f9ca45f82960b3ce380273cf26868bc842a72b7f"
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLED="$PROJECT/vendor/unified_agent_runtime"
TARGET="${1:-$PROJECT/.external/ploblem_ai}"
REPO_URL="${UNIFIED_AGENT_RUNTIME_REPO:-https://github.com/xm286947-cell/ploblem_ai.git}"

verify_snapshot() {
  candidate="$1"
  [ -f "$candidate/runtime/__init__.py" ] || return 1
  [ -f "$candidate/requirements-runtime-p0-test.txt" ] || return 1
  if [ -f "$candidate/RUNTIME_COMMIT" ]; then
    actual="$(tr -d '\r\n ' < "$candidate/RUNTIME_COMMIT")"
    [ "$actual" = "$EXPECTED" ] || { echo "ERROR Runtime snapshot mismatch: expected=$EXPECTED actual=$actual" >&2; return 2; }
    printf '%s\n' "$candidate"
    return 0
  fi
  if [ -d "$candidate/.git" ]; then
    actual="$(git -C "$candidate" rev-parse HEAD)"
    [ "$actual" = "$EXPECTED" ] || { echo "ERROR Runtime git mismatch: expected=$EXPECTED actual=$actual" >&2; return 2; }
    printf '%s\n' "$candidate"
    return 0
  fi
  return 1
}

if [ -n "${UNIFIED_AGENT_RUNTIME_ROOT:-}" ]; then
  verify_snapshot "$UNIFIED_AGENT_RUNTIME_ROOT"
  exit $?
fi

if [ -d "$BUNDLED" ]; then
  verify_snapshot "$BUNDLED"
  exit $?
fi

if [ ! -d "$TARGET/.git" ]; then
  echo "[setup] bundled Runtime not found; cloning Unified Agent Runtime -> $TARGET" >&2
  mkdir -p "$(dirname "$TARGET")"
  git clone "$REPO_URL" "$TARGET" >&2
fi
actual="$(git -C "$TARGET" rev-parse HEAD)"
if [ "$actual" != "$EXPECTED" ]; then
  echo "[setup] checkout pinned Runtime $EXPECTED" >&2
  git -C "$TARGET" fetch --all --tags --prune >&2
  git -C "$TARGET" checkout --detach "$EXPECTED" >&2
fi
verify_snapshot "$TARGET"
