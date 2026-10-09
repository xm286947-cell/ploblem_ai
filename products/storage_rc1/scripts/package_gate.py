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
release_manifest_path=ROOT/'knowledge_release/current/release_manifest.json'
release_objects_path=ROOT/'knowledge_release/current/knowledge_objects.json'
checks['FORMAL_RELEASE_AVAILABLE']=release_manifest_path.is_file()
formal_nand_domains=set()
formal_nand_release=False
if checks['FORMAL_RELEASE_AVAILABLE'] and release_objects_path.is_file():
 manifest=json.loads(release_manifest_path.read_text(encoding='utf-8'))
 objects=json.loads(release_objects_path.read_text(encoding='utf-8')).get('objects',[])
 formal_nand_release=(manifest.get('release_class') in {'FORMAL','FORMAL_KNOWLEDGE_RELEASE'}
                      and manifest.get('qualification_state')=='FORMAL')
 for obj in objects:
  if str(obj.get('device_type') or '').lower() not in {'nand flash','nand'}:
   continue
  if not obj.get('evidence_refs'):
   continue
  text=' '.join(str(obj.get(key) or '') for key in ('title','summary','content','semantic_class'))
  text+=' '+' '.join(map(str,obj.get('tags') or []))
  lowered=text.lower()
  if 'p/e' in lowered or 'endurance' in lowered: formal_nand_domains.add('PE_ENDURANCE')
  if 'retention' in lowered: formal_nand_domains.add('RETENTION')
  if 'ecc' in lowered or 'bit flip' in lowered: formal_nand_domains.add('ECC_BIT_FLIP')
  if 'bad block' in lowered or 'bad_block' in lowered: formal_nand_domains.add('BAD_BLOCK')
checks['FORMAL_NAND_RELEASE_READY']=formal_nand_release and formal_nand_domains=={'PE_ENDURANCE','RETENTION','ECC_BIT_FLIP','BAD_BLOCK'}
# Conservative secret scan over product config/source, ignoring examples/tests/baseline evidence.
secret_patterns=[re.compile(r'(?i)authorization\s*[:=]\s*bearer\s+[A-Za-z0-9._-]{12,}'),re.compile(r'\bsk-[A-Za-z0-9_-]{16,}\b')]
hits=[]
for base in ('config','storage_life','scripts'):
 for p in (ROOT/base).rglob('*'):
  if not p.is_file() or p.suffix.lower() in {'.pdf','.sqlite3','.pyc'}: continue
  text=p.read_text(encoding='utf-8',errors='ignore')
  if any(rx.search(text) for rx in secret_patterns): hits.append(str(p.relative_to(ROOT)))
checks['SECRET_SCAN']=not hits
out={'checks':checks,'secret_hits':hits,'package_gate':'PASS' if all(v for k,v in checks.items() if k not in {'FORMAL_RELEASE_AVAILABLE','FORMAL_NAND_RELEASE_READY'}) else 'FAIL','product_gate':'PENDING_FORMAL_NAND_KNOWLEDGE_RELEASE'}
if checks['FORMAL_NAND_RELEASE_READY']:
 out['product_gate']='READY_FOR_REAL_GOLDEN'
else:
 out['product_gate']='PENDING_FORMAL_NAND_KNOWLEDGE_RELEASE'
print(json.dumps(out,ensure_ascii=False,indent=2))
evidence_path=ROOT/'evidence/build/package_gate.json'
evidence_path.parent.mkdir(parents=True,exist_ok=True)
evidence_path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
sys.exit(0 if out['package_gate']=='PASS' else 2)
