"""W3 G1/G2 source identity and sequential-default browser contract.

Uses immutable W3 candidate ZIP and test-only fixture kit.
Explicitly no Publish/Real Provider, never touches users' Mac/8080/18781.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from urllib.request import Request,urlopen
from urllib.error import URLError,HTTPError
import zipfile

from playwright.sync_api import sync_playwright

SUPPORT=Path(__file__).resolve().parents[1]
DIST=SUPPORT/"dist"
OUTPUT=DIST/"browser_g1_g2_evidence"
OUTPUT.mkdir(parents=True,exist_ok=True)
CANDIDATE_NAME="HARDWARE_W3_INTERNAL_TEST_CANDIDATE_c7935e0"
CANDIDATE_SHA="42966120034b4e1e734c89066dfa60478c28685896a8b26fdc1181b43aaef138"
FIXTURE_NAME="HARDWARE_W3_T2_BROWSER_MOCK_FIXTURES_c7935e0"
FIXTURE_SHA="a59ef48eacd4f1ed7e78a407e6b0cf09d1493e073205f61884f60bb8818a4a8b"
SOURCE_SHA="c7935e00f1f0abe828b0090524bb14ba923f3635"
BASE="http://127.0.0.1:18782"
MOCK="http://127.0.0.1:18783"

def require_package(name, sha, unzip_here):
    file=DIST/(name+".zip")
    assert file.is_file(),file
    got=hashlib.sha256(file.read_bytes()).hexdigest()
    assert got==sha,f"ZIP_SHA_MISMATCH:{name}:{got}"
    with zipfile.ZipFile(file) as z:
        assert z.testzip() is None
        z.extractall(unzip_here)
    return file

def get(url, role=False):
    req=Request(url,method="GET",headers={"X-Hardware-Case-Role":"MAINTAINER"} if role else {})
    with urlopen(req,timeout=5) as resp:
        return resp.status,json.loads(resp.read())

def await_url(url,proc,timeout=75):
    deadline=time.monotonic()+timeout
    last=None
    while time.monotonic()<deadline:
        if proc.poll() is not None:
            raise RuntimeError("EARLY_EXIT:"+url+":rc="+str(proc.returncode))
        try:
            status,data=get(url)
            if status==200:return data
            last=str(data)
        except (URLError,HTTPError,TimeoutError) as exc:
            last=str(exc)
        time.sleep(.5)
    raise RuntimeError("SERVICE_NOT_READY:"+url+":"+str(last))

def start(command,env,cwd,logfile):
    log=open(logfile,"w",encoding="utf-8")
    kwargs={"creationflags":subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=="nt" else {"start_new_session":True}
    p=subprocess.Popen(command,env=env,cwd=cwd,stdout=log,stderr=subprocess.STDOUT,**kwargs)
    return p,log

def stop_group(proc,log):
    if proc.poll() is None:
        if os.name=="nt":
            # Windows must kill Python descendants before deleting extracted ZIP.
            subprocess.run(["taskkill","/PID",str(proc.pid),"/T","/F"],
                           stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                           text=True,timeout=15,check=False)
        else:
            try:os.killpg(proc.pid,signal.SIGTERM)
            except ProcessLookupError:pass
        try:proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if os.name=="nt":proc.kill()
            else:os.killpg(proc.pid,signal.SIGKILL)
            proc.wait(timeout=5)
    log.close()
    if os.name=="nt":time.sleep(2)

def evidence(batch,counters):
    records={
      "source_sha":SOURCE_SHA,
      "candidate_zip_sha256":CANDIDATE_SHA,
      "fixture_kit_sha256":FIXTURE_SHA,
      "status":"NOT_YET_PASS",
      "batch":batch,
      "mock_counters":counters,
      "real_provider":False,"production_8080_modified":False,
      "ui_triggered_write_operations":["POST /api/v2/hardware-cases/r1/workbench/batches (synthetic upload)",
                                      "POST /api/v2/hardware-cases/r1/workbench/batches/{id}/run-resume (mock only)"],
      "formal_publish":False
    }
    return records

with tempfile.TemporaryDirectory(prefix="w3_tse_browser_g3_") as folder:
    base=Path(folder)
    require_package(CANDIDATE_NAME,CANDIDATE_SHA,base)
    require_package(FIXTURE_NAME,FIXTURE_SHA,base/"fixture")
    product=base/CANDIDATE_NAME
    fixtures=base/"fixture"
    assert (product/"W3_TEST_CANDIDATE_MANIFEST.json").is_file()
    assert (fixtures/"A9903-MCU串口带载乱码.docx").is_file()
    assert (fixtures/"A9904-MCU串口输出配置异常.docx").is_file()
    # Separate source identity is necessary for the sequential A/B/A/B check.
    # The frozen kit ships two otherwise byte-identical synthetic DOCX.
    # Only this extracted test fixture copy is changed: an inert ZIP member,
    # leaving document.xml and all Evidence block IDs byte-for-byte identical.
    with zipfile.ZipFile(fixtures/"A9904-MCU串口输出配置异常.docx","a") as z:
        z.writestr("customXml/W3_G2_DISTINCT_TEST_SOURCE.txt",
                   "Synthetic W3 G2 Source Identity Delta. No content changes.")
    manifest=json.loads((product/"W3_TEST_CANDIDATE_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["source_commit"]==SOURCE_SHA and manifest["real_provider"]=="NOT_RUN"
    model=product/"config/runtime/hardware_w3_mock_only.example.yaml"
    assert "127.0.0.1:18783" in model.read_text(encoding="utf-8")
    isolated=base/"RUN_ISOLATED"
    isolated.mkdir()
    env={**os.environ,
         "HARDWARE_CASE_MODEL_CONFIG":str(model),
         "HARDWARE_R1_E2E_KNOWLEDGE_ENV":"NON_PROD",
         "HARDWARE_R1_E2E_KNOWLEDGE_MODE":"LOCAL_NON_PROD",
         "HARDWARE_R1_E2E_DATA_ROOT":str(isolated/"Data"),
         "HARDWARE_R1_E2E_BOOTSTRAP_PATH":str(isolated/"bootstrap.json"),
         "PYTHONUNBUFFERED":"1",
    }
    mock_p,mock_log=start([sys.executable,str(fixtures/"w3_tse_browser_mock_fixtures.py"),
                           "--product-root",str(product),"--out-dir",str(fixtures),"--serve"],
                          env,product,OUTPUT/"mock_server.log")
    web_p,web_log=None,None
    try:
        await_url(MOCK+"/__mock__/health",mock_p)
        web_p,web_log=start([sys.executable,str(product/"scripts/hardware_r1_e2e_validation_start.py"),
                             "--data-root",str(isolated/"Data"),
                             "--bootstrap",str(isolated/"bootstrap.json"),
                             "--host","127.0.0.1","--port","18782","--no-browser"],
                            env,product,OUTPUT/"web_server.log")
        ready=await_url(BASE+"/ready",web_p)
        assert ready["status"]=="READY",ready
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
            page=browser.new_page(viewport={"width":1440,"height":1100})
            failed_messages=[]
            page.on("pageerror",lambda e:failed_messages.append(str(e)))
            page.goto(BASE+"/p0/hardware-cases/knowledge-production",wait_until="domcontentloaded",timeout=30000)
            page.locator("[data-batch-files]").set_input_files(
                [str(fixtures/"A9903-MCU串口带载乱码.docx"),str(fixtures/"A9904-MCU串口输出配置异常.docx")]
            )
            page.wait_for_function(
                "() => { const s=document.querySelector('[data-batch-history]'); return s && s.value && s.value.startsWith('HWB-'); }",
                timeout=30000
            )
            batch_id=page.locator("[data-batch-history]").input_value()
            status,before=get(BASE+"/api/v2/hardware-cases/r1/workbench/batches/"+batch_id,True)
            assert status==200 and len(before["items"])==2,before
            assert all(i["result"]=="QUEUED" for i in before["items"]),before
            # G1 source identity and parser bound to both synthetic Word files.
            assert {x["business_case_id"] for x in before["items"]}=={"A9903","A9904"},before
            assert all(x["parse"]=="PASS" for x in before["items"]),before
            assert len({x["source_id"] for x in before["items"]})==2, "G2_REQUIRES_DISTINCT_SOURCES"
            assert all(x["source_file"].endswith(".docx") for x in before["items"]),before
            print("G1_MULTI_WORD_UPLOAD_SOURCE_IDENTITY=PASS")
            # G2: W2 sequential mode MUST remain the default; no opt-in PARALLEL.
            assert page.locator("[data-w3-execution-mode]").input_value()=="SEQUENTIAL"
            assert page.locator("[data-w3-concurrency]").is_disabled()
            assert page.locator("[data-run-batch]").is_enabled()
            page.locator("[data-run-batch]").click()
            deadline=time.monotonic()+110
            latest=before
            while time.monotonic()<deadline:
                _,latest=get(BASE+"/api/v2/hardware-cases/r1/workbench/batches/"+batch_id,True)
                if all(x["result"] not in ("RUNNING","QUEUED") for x in latest["items"]):break
                time.sleep(1)
            else:raise AssertionError("G3_RUN_TIMEOUT:"+json.dumps(latest,ensure_ascii=False)[:1800])
            _,counter=get(MOCK+"/__mock__/counters")
            payload=evidence(latest,counter)
            payload["page_errors"]=failed_messages
            payload["batch_id"]=batch_id
            payload["case_results"]={x["business_case_id"]:x["result"] for x in latest["items"]}
            payload["provider_calls_per_case"]={x["business_case_id"]:x.get("provider_calls") for x in latest["items"]}
            _,records=get(MOCK+"/__mock__/requests")
            order=[x.get("scenario_key") for x in records["data"]]
            print("G1_G2_ITEM_STATES="+json.dumps([{"case":x.get("business_case_id"),"result":x.get("result"),"calls":x.get("provider_calls"),"source_id":x.get("source_id"),"candidate_id":x.get("candidate_id"),"error_code":x.get("error_code")} for x in latest["items"]],ensure_ascii=False),flush=True)
            print("G1_G2_REQUEST_ORDER="+json.dumps(order),flush=True)
            assert order==["stage-a","stage-b","stage-a","stage-b"],order
            payload["call_order"]=order
            payload["default_execution_mode"]="SEQUENTIAL"
            payload["source_identity_test_delta"]="A9904 synthetic inert customXml member added only in extracted CI fixture"
            (OUTPUT/"G1_G2_REPORT.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
            page.screenshot(path=str(OUTPUT/"G1_G2_WORKBENCH.png"),full_page=True)
            print("G1_G2_BATCH="+batch_id)
            print("G1_G2_RESULTS="+json.dumps(payload["case_results"],ensure_ascii=False))
            print("G1_G2_PROVIDER_CALLS="+json.dumps(payload["provider_calls_per_case"],ensure_ascii=False))
            print("G1_G2_MOCK_COUNTS="+json.dumps(counter,ensure_ascii=False))
            print("G1_G2_PAGE_ERRORS="+str(len(failed_messages)))
            assert not failed_messages,failed_messages
            assert set(payload["case_results"])=={"A9903","A9904"},payload
            assert set(payload["case_results"].values()).issubset({"CANDIDATE_READY","REVIEW"}),payload
            assert counter.get("data",{}).get("stage-a",0)==2,counter
            assert counter.get("data",{}).get("stage-b",0)==2,counter
            assert counter.get("data",{}).get("default",0)==0,counter
            assert all(1 <= int(n) <= 4 for n in payload["provider_calls_per_case"].values()),payload
            print("G1_G2_BROWSER_SEQUENTIAL_GOLDEN=PASS")
            browser.close()
    finally:
        if web_p is not None:stop_group(web_p,web_log)
        stop_group(mock_p,mock_log)
