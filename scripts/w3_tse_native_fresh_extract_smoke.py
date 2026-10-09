"""Windows/macOS fresh-extract smoke for internal W3 test package.

Starts product-owned test launcher but sends *read-only requests* only.
No Agent Run, No candidate review, No publish, No external Provider.
"""
from __future__ import annotations
import hashlib, json, os, pathlib, shutil, subprocess, sys, tempfile, time, urllib.error, urllib.request, zipfile

SOURCE_SHA="c7935e00f1f0abe828b0090524bb14ba923f3635"
NAME="HARDWARE_W3_INTERNAL_TEST_CANDIDATE_c7935e0"
ROOT=pathlib.Path(__file__).resolve().parents[1]
dist=ROOT/"dist"
target=dist/(NAME+".zip")
assert target.is_file(), "CANDIDATE_NOT_BUILT"
expected_hash=(dist/(NAME+"_SHA256.txt")).read_text(encoding="utf-8").split("ZIP_SHA256=")[1].splitlines()[0]
real_hash=hashlib.sha256(target.read_bytes()).hexdigest()
assert real_hash==expected_hash,"CANDIDATE_SHA_MISMATCH"

def read(url,headers=None):
    request=urllib.request.Request(url,headers=headers or {},method="GET")
    with urllib.request.urlopen(request,timeout=5) as response:
        return response.status,response.read()

with tempfile.TemporaryDirectory(prefix="w3_candidate_native_") as folder:
    extracted=pathlib.Path(folder)
    with zipfile.ZipFile(target) as z:
        assert z.testzip() is None,"CANDIDATE_CRC_FAILURE"
        z.extractall(extracted)
    product=extracted/NAME
    manifest=json.loads((product/"W3_TEST_CANDIDATE_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["source_commit"]==SOURCE_SHA
    assert manifest["release_status"]=="INTERNAL_TEST_ONLY_NOT_FORMAL"
    assert manifest["win_mac_fresh_extract"]=="NOT_RUN"
    assert manifest["real_provider"]=="NOT_RUN"
    assert manifest["web_port"]==18782
    assert "127.0.0.1:18783" in (product/"config/runtime/hardware_w3_mock_only.example.yaml").read_text(encoding="utf-8")
    assert "data-w3-resume-batch" in (product/"quality_knowledge/web/templates/hardware_case_knowledge_production.html").read_text(encoding="utf-8")
    if sys.platform=="darwin":
        cmd=["/bin/sh",str(product/"START_HARDWARE_W3_MOCK_TEST.command")]
    elif os.name=="nt":
        cmd=["cmd","/c",str(product/"START_HARDWARE_W3_MOCK_TEST.bat")]
    else:
        raise RuntimeError("UNSUPPORTED_NATIVE_PLATFORM")
    # Python 3.11 installed via setup-python; no persistent user DB involved.
    logfile=dist/("native_start_"+sys.platform+".log")
    with logfile.open("w",encoding="utf-8") as log:
        worker=subprocess.Popen(cmd,cwd=product,stdout=log,stderr=subprocess.STDOUT)
        try:
            last_err=None
            deadline=time.monotonic()+70
            while time.monotonic()<deadline:
                if worker.poll() is not None:
                    raise RuntimeError("W3_NATIVE_LAUNCH_EARLY_EXIT:"+str(worker.returncode))
                try:
                    status,payload=read("http://127.0.0.1:18782/health")
                    if status==200: break
                except (urllib.error.URLError,TimeoutError) as exc:
                    last_err=str(exc)
                time.sleep(1)
            else:
                raise RuntimeError("W3_NATIVE_START_TIMEOUT:"+str(last_err))
            results={}
            for key,path in [
                ("HEALTH","/health"),
                ("READY","/ready"),
                ("WORKBENCH","/p0/hardware-cases/knowledge-production"),
                ("WORKBENCH_API","/api/v2/hardware-cases/r1/workbench/batches"),
            ]:
                headers={"X-Hardware-Case-Role":"MAINTAINER"} if key=="WORKBENCH_API" else {}
                status,body=read("http://127.0.0.1:18782"+path,headers=headers)
                results[key]=status
                assert status==200, f"{key}_HTTP_{status}:{body[:300]!r}"
                if key=="WORKBENCH":
                    assert b'data-w3-resume-batch' in body,"W3_RESUME_CONTROL_MISSING"
                    assert b'data-w3-execution-mode' in body,"W3_CONCURRENCY_UI_MISSING"
                if key=="READY":
                    result=json.loads(body)
                    assert result.get("status") in ("READY","PASS","OK"),result
            print("SOURCE_SHA="+SOURCE_SHA)
            print("PACKAGE_SHA256="+real_hash)
            print("NATIVE_SYSTEM="+sys.platform)
            print("RESULTS="+json.dumps(results,sort_keys=True))
            print("MOCK_PROVIDER_CALLS=0 (never contacted)")
            print("AGENT_RUN_POSTS=0")
            print("REVIEW_AND_PUBLISH_POSTS=0")
            print("RESULT=NATIVE_FRESH_EXTRACT_READ_ONLY_PASS")
        finally:
            if worker.poll() is None:
                if os.name == "nt":
                    # A .bat launcher starts a child Python process; killing
                    # only cmd.exe leaves its child holding the extracted
                    # directory open. Terminate the complete test process tree.
                    result = subprocess.run(
                        ["taskkill", "/PID", str(worker.pid), "/T", "/F"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, timeout=15, check=False,
                    )
                    print("WINDOWS_TEST_PROCESS_TREE_CLEANUP=" + str(result.returncode))
                else:
                    worker.terminate()
                try:
                    worker.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait(timeout=5)
            if os.name == "nt":
                # NTFS file handles can close slightly after taskkill exits.
                time.sleep(2)
    if worker.returncode not in (None,0,-15,1):
        print("NATIVE_TERMINATION_CODE="+str(worker.returncode))
