"""Check standalone W3 TSE mock fixture kit and live HTTP contract."""
from __future__ import annotations
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import time
from urllib.request import Request,urlopen
from urllib.error import URLError
import zipfile

ROOT=Path(__file__).resolve().parents[3]
PRODUCT=ROOT/"product"
KIT_SCRIPT=ROOT/"support"/"scripts"/"w3_tse_browser_mock_fixtures.py"
def get(url):
    with urlopen(url,timeout=4) as resp:
        assert resp.status==200
        return json.loads(resp.read())
def post(url,body):
    req=Request(url,method="POST",headers={"Content-Type":"application/json","Authorization":"Bearer W3_TEST_DUMMY_TOKEN_ONLY"},data=json.dumps(body,ensure_ascii=False).encode())
    with urlopen(req,timeout=4) as resp:
        assert resp.status==200
        return json.loads(resp.read())

def test_tse_browser_fixture_matches_actual_word_and_stage_contract(tmp_path):
    out=tmp_path/"w3_tse_browser_g3"
    done=subprocess.run(
        [sys.executable,str(KIT_SCRIPT),"--product-root",str(PRODUCT),"--out-dir",str(out)],
        capture_output=True,text=True,timeout=35,
    )
    assert done.returncode==0,done.stdout+done.stderr
    assert "FIXTURE_GATE=PASS" in done.stdout,done.stdout
    manifest=json.loads((out/"T2_FIXTURE_MANIFEST.json").read_text(encoding="utf-8"))
    assert len(manifest["cases"])==2
    assert {x["business_case_id"] for x in manifest["cases"]}=={"A9903","A9904"}
    assert all(len(x["evidence_block_ids"])==5 for x in manifest["cases"])
    for row in manifest["cases"]:
        with zipfile.ZipFile(out/row["file"]) as z:
            assert z.testzip() is None
            assert z.read("word/document.xml")
    assert json.loads((out/"stage_a_fixture.json").read_text(encoding="utf-8"))["facts"]["root_cause"]["status"]=="EXTRACTED"


def test_tse_browser_mock_server_can_respond_with_stage_specific_json(tmp_path):
    out=tmp_path/"w3_tse_browser_http"
    server=subprocess.Popen(
        [sys.executable,str(KIT_SCRIPT),"--product-root",str(PRODUCT),"--out-dir",str(out),"--serve"],
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            if server.poll() is not None:
                raise AssertionError("TSE_MOCK_SERVER_EXITED_EARLY")
            try:
                response=get("http://127.0.0.1:18783/__mock__/health")
                assert response["status"]=="ok"
                break
            except (URLError,TimeoutError):
                time.sleep(.2)
        else:raise AssertionError("TSE_MOCK_SERVER_NOT_READY")
        from_root=PRODUCT/"services"/"hardware_case_markdown_agent.py"
        assert from_root.is_file()
        a=post("http://127.0.0.1:18783/v1/chat/completions",
               {"model":"mock-gpt","messages":[
                 {"role":"system","content":"synthetic Stage A"},
                 {"role":"user","content":json.dumps({"input_contract":"hardware-case-r1-agent-input/v3"})}
                ]})
        b=post("http://127.0.0.1:18783/v1/chat/completions",
               {"model":"mock-gpt","messages":[
                 {"role":"system","content":"synthetic Stage B"},
                 {"role":"user","content":json.dumps({"input_contract":"hardware-case-r1-reuse-input/v1"})}
                ]})
        text_a=json.loads(a["choices"][0]["message"]["content"])
        text_b=json.loads(b["choices"][0]["message"]["content"])
        assert "facts" in text_a and "engineering_context" in text_a
        assert "reusable_knowledge_candidate" in text_b
        counts=get("http://127.0.0.1:18783/__mock__/counters")["data"]
        assert counts.get("stage-a")==1 and counts.get("stage-b")==1,counts
        assert counts.get("default",0)==0,counts
    finally:
        if server.poll() is None:
            server.terminate()
            try:server.wait(timeout=6)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=3)
        if server.stdout:server.stdout.close()
