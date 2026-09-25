from __future__ import annotations
import json, re, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
checks={}
html=(ROOT/'storage_life/index.html').read_text(encoding='utf-8')
for p in range(1,9): checks[f'P{p:02d}_UI']=f'P{p:02d} ' in html
checks['RUN_WINDOWS']=(ROOT/'run_windows.bat').is_file()
checks['RUNTIME']=(ROOT/'vendor/unified_agent_runtime/runtime/__init__.py').is_file()
checks['KNOWLEDGE_CONSUMER']=(ROOT/'storage_life/knowledge_release.py').is_file()
checks['NO_SAMPLE_DEFAULT']='knowledge_release\\seed' not in (ROOT/'run_windows.bat').read_text(encoding='utf-8',errors='ignore')
checks['FORMAL_RELEASE_AVAILABLE']=(ROOT/'knowledge_release/current/release_manifest.json').is_file()
# Conservative secret scan over product config/source, ignoring examples/tests/baseline evidence.
secret_patterns=[re.compile(r'(?i)authorization\s*[:=]\s*bearer\s+[A-Za-z0-9._-]{12,}'),re.compile(r'\bsk-[A-Za-z0-9_-]{16,}\b')]
hits=[]
for base in ('config','storage_life','scripts'):
 for p in (ROOT/base).rglob('*'):
  if not p.is_file() or p.suffix.lower() in {'.pdf','.sqlite3','.pyc'}: continue
  text=p.read_text(encoding='utf-8',errors='ignore')
  if any(rx.search(text) for rx in secret_patterns): hits.append(str(p.relative_to(ROOT)))
checks['SECRET_SCAN']=not hits
out={'checks':checks,'secret_hits':hits,'package_gate':'PASS' if all(v for k,v in checks.items() if k!='FORMAL_RELEASE_AVAILABLE') else 'FAIL','product_gate':'PENDING' if not checks['FORMAL_RELEASE_AVAILABLE'] else 'READY_FOR_REAL_GOLDEN'}
print(json.dumps(out,ensure_ascii=False,indent=2))
(ROOT/'evidence/build/package_gate.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
sys.exit(0 if out['package_gate']=='PASS' else 2)
