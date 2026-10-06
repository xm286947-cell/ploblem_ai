from __future__ import annotations
import hashlib, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

BASE="209593860110abf6d047240aa99ad22b4e983acd"
TARGET="00fa9588adf98cacc60f1c9ef7b853fc6923b3a8"
PATCH_ID="HARDWARE_R1_PUBLISH_RECONCILIATION_UI_HOTFIX_00fa9588"
FILES=[
 "quality_knowledge/web/static/hardware_case_knowledge_production.js",
 "quality_knowledge/web/templates/hardware_case_knowledge_production.html",
 "services/hardware_r1_knowledge_promotion.py",
]
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"dist"/"hotfix"
PAYLOAD=OUT/"payload"

def sha_bytes(b:bytes)->str:
    return hashlib.sha256(b).hexdigest()

def git_show(ref:str,path:str)->bytes:
    return subprocess.check_output(["git","show",f"{ref}:{path}"],cwd=ROOT)

def sha_file(p:Path)->str:
    h=hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda:f.read(1024*1024),b""):
            h.update(c)
    return h.hexdigest()

def main()->int:
    if OUT.exists():
        shutil.rmtree(OUT)
    PAYLOAD.mkdir(parents=True)
    meta=[]
    for rel in FILES:
        before=git_show(BASE,rel)
        after=git_show(TARGET,rel)
        if before==after:
            raise RuntimeError("expected changed runtime file: "+rel)
        dst=PAYLOAD/rel
        dst.parent.mkdir(parents=True,exist_ok=True)
        dst.write_bytes(after)
        meta.append({
          "path":rel,
          "base_sha256":sha_bytes(before),
          "target_sha256":sha_bytes(after),
          "base_size":len(before),
          "target_size":len(after),
        })
    manifest={
      "patch_id":PATCH_ID,
      "repo":"xm286947-cell/ploblem_ai",
      "source_pr":484,
      "base_head":BASE,
      "target_head":TARGET,
      "purpose":"Expose supported PUBLISH reconciliation in product UI without retrying publish.",
      "scope":[
        "Expose item-scoped reconciliation state",
        "Show publish reconciliation guidance and button",
        "Call existing /promotion/reconcile only",
        "Block duplicate Publish/Verify/Projection while reconciliation is pending",
        "Resume Verify -> projection after reconciliation",
        "Bump production JS asset identity to v1.7",
      ],
      "excluded":[
        "No automatic Publish retry",
        "No direct database write",
        "No automatic A0152/A0207 projection generation",
        "No Expected change",
        "No reconciliation algorithm change",
        "No Runtime/Prompt/Schema/Stage A/Stage B change",
      ],
      "ci":{
        "premerge_pr":484,
        "premerge_single_package_e2e_run":37471095561,
        "postmerge_single_package_e2e_run":37471395816,
        "postmerge_durable_asset_run":37471395834,
        "postmerge_publish_idempotency_run":37471395773,
        "python_regression":"73 passed",
        "js_regression":"12 passed / 0 failed",
        "windows_native":"PASS",
        "macos_native":"PASS",
        "fresh_extract":"PASS",
        "reproducible":"PASS",
        "durable_asset_win_mac_ubuntu":"PASS",
        "publish_idempotency_win_mac_ubuntu":"PASS",
      },
      "files":meta,
    }
    (OUT/"PATCH_MANIFEST.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    apply_py=r"""from __future__ import annotations
import hashlib,json,shutil,sys
from datetime import datetime
from pathlib import Path
PATCH_DIR=Path(__file__).resolve().parent
M=json.loads((PATCH_DIR/"PATCH_MANIFEST.json").read_text(encoding="utf-8"))
def sha256(p):
    h=hashlib.sha256()
    with Path(p).open("rb") as f:
        for c in iter(lambda:f.read(1024*1024),b""): h.update(c)
    return h.hexdigest()
def target_root():
    if len(sys.argv)>1: return Path(sys.argv[1]).expanduser().resolve()
    for c in (PATCH_DIR.parent,Path.cwd()):
        if (c/"quality_knowledge"/"web").is_dir() and (c/"services").is_dir(): return c.resolve()
    raise SystemExit("Hardware R1 product root not found. Use: python APPLY_HOTFIX.py <product-root>")
target=target_root()
plan=[]
for item in M["files"]:
    rel=Path(item["path"]); src=PATCH_DIR/"payload"/rel; dst=target/rel
    if not src.is_file() or not dst.is_file(): raise SystemExit("Missing patch/target file; nothing changed: "+str(rel))
    current=sha256(dst)
    if current==item["target_sha256"]: plan.append((item,rel,src,dst,"ALREADY"))
    elif current==item["base_sha256"]: plan.append((item,rel,src,dst,"APPLY"))
    else: raise SystemExit("Baseline mismatch; nothing changed: "+str(rel)+"\nEXPECTED_BASE_SHA256="+item["base_sha256"]+"\nACTUAL_SHA256="+current)
todo=[x for x in plan if x[4]=="APPLY"]
if not todo:
    print("PATCH_APPLY=PASS\nIDEMPOTENT_REUSE=YES\nTARGET_HEAD="+M["target_head"]); raise SystemExit(0)
backup=target/".hardware_r1_patch_backup"/(datetime.now().strftime("%Y%m%d_%H%M%S")+"_publish_reconciliation_ui")
for item,rel,src,dst,_ in todo:
    b=backup/rel; b.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(dst,b)
try:
    for item,rel,src,dst,_ in todo:
        shutil.copy2(src,dst)
        if sha256(dst)!=item["target_sha256"]: raise RuntimeError("Target SHA mismatch: "+str(rel))
except Exception as exc:
    for item,rel,src,dst,_ in todo:
        b=backup/rel
        if b.is_file(): shutil.copy2(b,dst)
    raise SystemExit("PATCH_APPLY=FAILED_AND_ROLLED_BACK\n"+str(exc))
print("PATCH_APPLY=PASS")
print("IDEMPOTENT_REUSE=NO")
print("FILES_APPLIED="+str(len(todo)))
print("BACKUP="+str(backup))
print("TARGET_HEAD="+M["target_head"])
print("NEXT=RESTART_AND_HANDLE_A0152_PUBLISH_RECONCILIATION")
"""
    (OUT/"APPLY_HOTFIX.py").write_text(apply_py,encoding="utf-8")
    (OUT/"APPLY_HOTFIX.command").write_text('#!/bin/sh\nset -eu\nROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\ncd "$ROOT"\n"${PYTHON:-python3}" APPLY_HOTFIX.py "$@"\nprintf "\\nHotfix applied. Restart Hardware R1.\\n"\n',encoding="utf-8")
    os.chmod(OUT/"APPLY_HOTFIX.command",0o755)
    (OUT/"APPLY_HOTFIX.bat").write_text('@echo off\r\nsetlocal\r\ncd /d "%~dp0"\r\nif defined PYTHON (\r\n  "%PYTHON%" APPLY_HOTFIX.py %*\r\n) else (\r\n  py -3 APPLY_HOTFIX.py %*\r\n)\r\nif errorlevel 1 (\r\n  echo Hotfix failed.\r\n  pause\r\n  exit /b 1\r\n)\r\necho Hotfix applied. Restart Hardware R1.\r\npause\r\n',encoding="utf-8")
    (OUT/"README_应用说明.txt").write_text(
"""Hardware R1 发布对账 UI 现场热修
BASE_HEAD=209593860110abf6d047240aa99ad22b4e983acd
TARGET_HEAD=00fa9588adf98cacc60f1c9ef7b853fc6923b3a8
SOURCE_PR=#484

用途：
A0152 发布返回 PUBLISH_RECONCILIATION_REQUIRED 时，产品 UI 显示“处理发布对账”。
该动作只调用已有 /promotion/reconcile，不会再次发起 Publish。

应用后：
A0152：处理发布对账 -> 验证发布结果 -> 生成检索数据
A0207：正常发布 -> 验证发布结果 -> 生成检索数据
两条 Projection 就绪后恢复 8 个 Golden Case。

明确不做：
不自动重试 Publish；不直接写数据库；不自动补 Projection；不修改 Expected。
""",encoding="utf-8")
    diff=subprocess.check_output(["git","diff",f"{BASE}..{TARGET}","--",*FILES],cwd=ROOT)
    (OUT/"SOURCE_DIFF.patch").write_bytes(diff)
    (OUT/"SOURCE_REFERENCE.txt").write_text(
"""REPO=xm286947-cell/ploblem_ai
BASE_HEAD=209593860110abf6d047240aa99ad22b4e983acd
TARGET_HEAD=00fa9588adf98cacc60f1c9ef7b853fc6923b3a8
PR=#484
PREMERGE_SINGLE_PACKAGE_E2E_RUN=37471095561
POSTMERGE_SINGLE_PACKAGE_E2E_RUN=37471395816
POSTMERGE_DURABLE_ASSET_RUN=37471395834
POSTMERGE_PUBLISH_IDEMPOTENCY_RUN=37471395773
PYTHON=73 passed
JS=12 passed / 0 failed
WINDOWS=PASS
MACOS=PASS
FRESH_EXTRACT=PASS
REPRODUCIBLE=PASS
""",encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="reconcile-hotfix-smoke-") as td:
        product=Path(td)/"product"
        for rel in FILES:
            dst=product/rel; dst.parent.mkdir(parents=True,exist_ok=True); dst.write_bytes(git_show(BASE,rel))
        result=subprocess.run([sys.executable,str(OUT/"APPLY_HOTFIX.py"),str(product)],check=True,text=True,capture_output=True)
        for item in meta:
            if sha_file(product/item["path"])!=item["target_sha256"]: raise RuntimeError("smoke mismatch: "+item["path"])
        (OUT/"SMOKE_RESULT.txt").write_text(
            "PATCH_SMOKE=PASS\nBASE_HEAD="+BASE+"\nTARGET_HEAD="+TARGET+"\nFILES_VERIFIED=3/3\nDATABASE_MUTATION=NO\nEXPECTED_MUTATION=NO\n"+result.stdout,
            encoding="utf-8")
    print("PATCH_EXPORT=PASS")
    print("PATCH_DIR="+str(OUT))
    return 0
if __name__=="__main__":
    raise SystemExit(main())
