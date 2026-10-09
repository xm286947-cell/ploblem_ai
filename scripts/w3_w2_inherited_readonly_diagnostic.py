"""Read-only W2 inherited Word/Stage A failure diagnostics against frozen source."""
from pathlib import Path
import importlib.util, hashlib, json, subprocess, sys, tempfile

product=Path(sys.argv[1]).resolve()
assert product.name=="product" and (product/"services/hardware_case_word.py").is_file()
sys.path.insert(0,str(product))
test_file=product/"tests/test_hardware_r1_word_import_web.py"
spec=importlib.util.spec_from_file_location("w2_word_existing_tests",test_file)
assert spec and spec.loader
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
from services.hardware_case_word import parse_docx,HardwareWordParseError

with tempfile.TemporaryDirectory(prefix="w2_inherited_readonly_") as td:
    root=Path(td)
    source=root/"A12345-Flash启动异常.docx"
    payload=mod._docx(source)
    try:
        parsed=parse_docx(source).to_snapshot()
        print("DOCX_PARSE_DIRECT=PASS blocks="+str(len(parsed["structure"]["blocks"])))
    except Exception as e:
        print("DOCX_PARSE_DIRECT=FAILED TYPE="+type(e).__name__+" CODE="+str(getattr(e,"code",None))+" MESSAGE="+str(e)[:200])
    client=mod._client(root)
    request={"files":{"file":(source.name,payload,"application/vnd.openxmlformats-officedocument.wordprocessingml.document")},"headers":mod.MAINTAINER}
    response=client.post("/api/v2/hardware-cases/r1/word-snapshot",**request)
    print("W2_WORD_SNAPSHOT_HTTP="+str(response.status_code))
    try:
        body=response.json()
    except Exception:
        body={"message":response.text[:300]}
    print("W2_WORD_SNAPSHOT_DETAIL="+str(body.get("detail","NONE"))[:250])
    print("W2_WORD_SNAPSHOT_TEST_FILE_SHA256="+hashlib.sha256(payload).hexdigest())
    # No auth/key/env dump.
    assert response.status_code in (200,400,409,503),response.status_code
    if response.status_code==400:
        assert body.get("detail"),"W2_SOURCE_HTTP_400_WITHOUT_FAIL_CLOSED_DETAIL"
for file in ["config/runtime/agents/hardware_case.r1_case_extract.yaml","prompts/runtime/hardware_case/r1_case_extract_v1.md"]:
    p=product/file
    if p.exists():
        raw=p.read_bytes()
        print("STAGE_A_FILE="+file+" BYTES="+str(len(raw))+" CRLF="+str(raw.count(b"\\r\\n"))+" LF="+str(raw.count(b"\\n"))+" SHA="+hashlib.sha256(raw).hexdigest())
print("DIAGNOSTIC_DONE=YES")
