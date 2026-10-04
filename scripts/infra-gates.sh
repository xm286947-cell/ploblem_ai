#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p evidence
./.venv/bin/pytest -q tests/pkr | tee evidence/pytest-results.txt
./.venv/bin/python scripts/verify_remote_ollama.py | tee evidence/ollama_remote_probe.json
python3 - <<'PY'
from pathlib import Path
import re
import subprocess

tracked = subprocess.check_output(['git', 'ls-files', '-z']).decode().split('\0')
scope = {'Dockerfile', 'compose.yaml', '.dockerignore'}
scope.update(name for name in tracked if name.startswith((
    'public_knowledge_rag/', 'tests/pkr/', 'scripts/e2e_smoke.py',
    'scripts/fail_closed_smoke.sh', 'scripts/health.sh', 'scripts/infra-gates.sh',
    'scripts/verify_remote_ollama.py', 'docs/PUBLIC_KNOWLEDGE_RAG_INFRA_001.md',
    'evidence/', 'start.sh', 'stop.sh')))
bad = []
private_key_marker = '-----BEGIN ' + 'PRIVATE KEY-----'
bearer_pattern = r'(?i)\bBea' + r'rer\s+[A-Za-z0-9._~+/=-]{24,}'
if '.env' in tracked:
    bad.append('.env is tracked')
for name in scope:
    path = Path(name)
    try:
        text = path.read_text(encoding='utf-8')
    except (UnicodeError, OSError):
        continue
    if private_key_marker in text or re.search(bearer_pattern, text):
        bad.append(str(path))
if bad:
    raise SystemExit('credential-like content found in tracked workspace: ' + ', '.join(bad))
print('SECRET_ISOLATION=PASS')
PY
