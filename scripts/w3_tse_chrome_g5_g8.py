"""W3 G5-G8 browser failure isolation, review gate, refresh and safe audit.

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
OUTPUT=DIST/"browser_g5_g8_evidence"
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
    p=subprocess.Popen(command,env=env,cwd=cwd,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    return p,log

def stop_group(proc,log):
    if proc.poll() is None:
        try:os.killpg(proc.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        try:proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid,signal.SIGKILL)
            proc.wait(timeout=5)
    log.close()

def evidence(batch,counters):
    records={
      "source_sha":SOURCE_SHA,
      "candidate_zip_sha256":CANDIDATE_SHA,
      "fixture_kit_sha256":FIXTURE_SHA,
      "status":"G5_G8_IN_PROGRESS",
      "batch":batch,
      "mock_counters":counters,
      "real_provider":False,"production_8080_modified":False,
      "ui_triggered_write_operations":["POST /api/v2/hardware-cases/r1/workbench/batches (G5 synthetic upload)",
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
    assert (fixtures/"w3_tse_browser_mock_fixtures.py").is_file()
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
    mock_p,mock_log=start([sys.executable,str(SUPPORT/"scripts/w3_tse_g5_mock_server.py"),
                           str(product),str(fixtures)],
                          env,product,OUTPUT/"mock_server.log")
    web_p,web_log=None,None
    try:
        await_url(MOCK+"/__mock__/health",mock_p)
        assert (fixtures/"A9915-G5阶段B故障注入.docx").is_file()
        assert (fixtures/"A9916-G5正常案例.docx").is_file()
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
                [str(fixtures/"A9915-G5阶段B故障注入.docx"),str(fixtures/"A9916-G5正常案例.docx")]
            )
            page.wait_for_function(
                "() => { const s=document.querySelector('[data-batch-history]'); return s && s.value && s.value.startsWith('HWB-'); }",
                timeout=30000
            )
            batch_id=page.locator("[data-batch-history]").input_value()
            status,before=get(BASE+"/api/v2/hardware-cases/r1/workbench/batches/"+batch_id,True)
            assert status==200 and len(before["items"])==2,before
            assert all(i["result"]=="QUEUED" for i in before["items"]),before
            page.locator("[data-w3-execution-mode]").select_option("PARALLEL")
            page.locator("[data-w3-concurrency]").select_option("2")
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
            assert set(payload["case_results"])=={"A9915","A9916"},payload
            assert payload["case_results"]["A9915"]=="FAILED",payload
            assert payload["case_results"]["A9916"] in ("CANDIDATE_READY","REVIEW"),payload
            fail_item=next(x for x in latest["items"] if x["business_case_id"]=="A9915")
            pass_item=next(x for x in latest["items"] if x["business_case_id"]=="A9916")
            assert fail_item["stage_a"]=="PASS" and fail_item["stage_b"]=="FAILED",fail_item
            assert fail_item["failed_stage"]=="STAGE_B",fail_item
            assert pass_item["stage_a"]=="PASS" and pass_item["stage_b"]=="PASS",pass_item
            assert 1 <= int(fail_item["provider_calls"]) <= 4
            assert int(pass_item["provider_calls"]) == 2
            counts=counter["data"]
            assert counts.get("stage-a-failcase",0)==1,counts
            assert counts.get("stage-a-good",0)==1,counts
            assert 1 <= counts.get("stage-b-failcase",0) <= 2,counts
            assert counts.get("stage-b-good",0)==1,counts
            assert counts.get("default",0)==0,counts
            print("G5_FAIL_CASE="+fail_item["result"]+" stage="+str(fail_item["failed_stage"]))
            print("G5_PASS_CASE="+pass_item["result"])
            print("G5_PROVIDER_COUNTS="+json.dumps(counts,sort_keys=True))
            print("G5_FAILURE_ISOLATION=PASS")

            # G6: Negative Human Review/Publish gate checks. Absolutely no POST /publish.
            page.locator('[data-open-item="'+pass_item["item_id"]+'"]').click()
            page.locator("[data-case-detail]").wait_for(state="visible",timeout=10000)
            panel=page.locator("[data-e2e-promotion]")
            panel.wait_for(state="visible",timeout=10000)
            page.wait_for_function("""() => {
                const b=document.querySelector('[data-promotion-publish]');
                return b && b.disabled;
            }""",timeout=10000)
            assert page.locator("[data-promotion-publish]").is_disabled()
            good_before=get(BASE+"/api/v2/hardware-cases/r1/workbench/items/"+pass_item["item_id"],True)[1]
            assert good_before.get("candidate_id"),good_before
            page.screenshot(path=str(OUTPUT/"G6_PUBLISH_DISABLED.png"),full_page=True)
            print("G6_UNREVIEWED_PUBLISH_BUTTON_DISABLED=PASS")
            # Failed case has no published candidate and cannot initiate promotion.
            page.locator("[data-return-batch]").click()
            page.locator('[data-open-item="'+fail_item["item_id"]+'"]').click()
            page.locator("[data-case-detail]").wait_for(state="visible",timeout=10000)
            assert page.locator("[data-e2e-promotion]").is_hidden()
            print("G6_FAILED_CASE_PROMOTION_HIDDEN=PASS")
            # Absolutely no submit of review/publish or mutation of formal knowledge.

            # G7: browser refresh and repeat Batch run/resume (no QUEUED items)
            # must not call Provider again, lose Candidate, or retry failed Stage B.
            page.reload(wait_until="domcontentloaded")
            page.locator("[data-batch-history]").select_option(batch_id)
            page.wait_for_function("""(id) => document.querySelector('[data-batch-ref]')?.textContent.includes(id)""",
                                   arg=batch_id,timeout=10000)
            _,refreshed=get(BASE+"/api/v2/hardware-cases/r1/workbench/batches/"+batch_id,True)
            assert {(i["business_case_id"],i["result"]) for i in refreshed["items"]} == \
                   {(i["business_case_id"],i["result"]) for i in latest["items"]}
            assert page.locator("[data-run-batch]").is_enabled()
            # This is an opt-in Mock-only repeat against a completed Batch; not a failed-stage retry.
            page.locator("[data-run-batch]").click()
            page.wait_for_function("""() => document.querySelector('[data-workbench-message]')?.textContent.includes('本次导入任务处理完成')""",
                                   timeout=30000)
            _,replayed=get(BASE+"/api/v2/hardware-cases/r1/workbench/batches/"+batch_id,True)
            assert {(i["business_case_id"],i["result"]) for i in replayed["items"]} == \
                   {(i["business_case_id"],i["result"]) for i in latest["items"]}
            assert next(i for i in replayed["items"] if i["business_case_id"]=="A9916")["candidate_id"] == pass_item["candidate_id"]
            _,counters_after=get(MOCK+"/__mock__/counters")
            assert counters_after==counter,(counter,counters_after)
            print("G7_REFRESH_AND_REPEATED_EMPTY_RESUME_ZERO_PROVIDER_CALLS=PASS")

            # G8: Mock request audit redacts Authorization. Never persist provider tokens.
            _,request_data=get(MOCK+"/__mock__/requests")
            requests=request_data["data"]
            assert len(requests)==sum(counts.values()),(len(requests),counts)
            serialized=json.dumps(request_data,ensure_ascii=False)
            assert "W3_TEST_DUMMY_TOKEN_ONLY" not in serialized,"MOCK_AUDIT_LEAKS_DUMMY_TOKEN"
            assert "Authorization: Bearer" not in serialized,"MOCK_AUDIT_LEAKS_AUTH"
            assert not failed_messages,failed_messages
            for request in requests:
                headers=request.get("headers") or {}
                for k,v in headers.items():
                    if k.lower()=="authorization":
                        assert v=="[REDACTED]",(k,v)
            payload["g5"]="PASS"
            payload["g6"]="PASS"
            payload["g7"]="PASS"
            payload["g8"]="PASS"
            payload["status"]="G5_G8_CI_PASS"
            payload["request_audit"]=[{"scenario_key":request.get("scenario_key"),
                                      "method":request.get("method"),
                                      "path":request.get("path")} for request in requests]
            payload["provider_call_counts_after_replay"]=counters_after
            (OUTPUT/"G5_G8_REPORT.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
            page.screenshot(path=str(OUTPUT/"G7_G8_WORKBENCH.png"),full_page=True)
            print("G8_MOCK_AUTH_HEADER_REDACTED=PASS")
            print("G5_G8_BROWSER_GOLDEN=PASS")
            browser.close()
    finally:
        if web_p is not None:stop_group(web_p,web_log)
        stop_group(mock_p,mock_log)
