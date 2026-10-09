"""Hardware W3 T2: isolated fixture-bound OpenAI Mock for browser G3/G5.

Test support ONLY. Uses frozen product Mock, JSON Schema and DOCX parser.
Defaults to fixture creation; server starts only with explicit --serve.
Never connects to a real provider, and does not call product Run/Publish.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile

DOC_PARAGRAPHS = (
    "本机 MCU → 串口屏经常乱码；PC / 其他 MCU 正常。",
    "空载 TX ≈3.3V；带载 ≈1.6V。",
    "MCU TX 默认弱上拉，驱动能力不足。",
    "弱上拉 → 推挽输出。",
    "长期可靠性测试未再复现。",
)
FILES = (
    "A9903-MCU串口带载乱码.docx",
    "A9904-MCU串口输出配置异常.docx",
)
MISSING = {"value": None, "status": "MISSING", "evidence_block_ids": []}
FACT_NAMES = (
    "background","symptom","impact","occurrence_condition",
    "analysis_process","failure_mode","root_cause",
    "failure_mechanism","actions","verification_result","conclusion",
)
CONTEXT_NAMES = ("primary_subject","component_or_device","interface","signal","peer_device_or_load")
DERIVED_NAMES = (
    "engineering_rule","design_constraint","diagnostic_clue",
    "verification_method","applicability","conclusion",
)

def field(value=None, refs=None):
    return {"value": value, "status": "EXTRACTED" if value is not None else "MISSING",
            "evidence_block_ids": list(refs or [])}

def reusable(value=None, derived=None, refs=None):
    return {**field(value,refs), "derived_from_fields": list(derived or [])}

def stage_a_fixture(ids):
    b1,b2,b3,b4,b5=ids
    facts={k:field() for k in FACT_NAMES}
    facts.update({
        "symptom":field(DOC_PARAGRAPHS[0],[b1]),
        "analysis_process":field(DOC_PARAGRAPHS[1],[b2]),
        "root_cause":field(DOC_PARAGRAPHS[2],[b3]),
        "actions":field(DOC_PARAGRAPHS[3],[b4]),
        "verification_result":field(DOC_PARAGRAPHS[4],[b5]),
    })
    return {"engineering_context":{
        "primary_subject":field("MCU",[b1]),
        "component_or_device":field("MCU",[b1]),
        "interface":field("串口屏",[b1]),
        "signal":field("TX",[b2]),
        "peer_device_or_load":field("串口屏",[b1]),
        "key_parameters":[]
    }, "facts":facts}

def stage_b_fixture(ids):
    b1,b2,b3,b4,b5=ids
    data={k:reusable() for k in DERIVED_NAMES}
    data.update({
        "engineering_rule":reusable("UART TX 带载电平不足时应检查输出驱动配置。",
            ["root_cause","actions"],[b3,b4]),
        "diagnostic_clue":reusable("空载正常、带载电平明显下降是驱动能力不足的诊断线索。",
            ["analysis_process","root_cause"],[b2,b3]),
        "verification_method":reusable("修改输出模式后执行长期可靠性测试。",
            ["actions","verification_result"],[b4,b5]),
    })
    return {"reusable_knowledge_candidate":data}

def make_docx(path):
    text="".join("<w:p><w:r><w:t>{}</w:t></w:r></w:p>".format(escape(p))
                 for p in DOC_PARAGRAPHS)
    xml=("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
         "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
         "<w:body>"+text+"</w:body></w:document>")
    with ZipFile(path,"w",compression=ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml",xml)

def prepare(product_root,out):
    sys.path.insert(0,str(product_root))
    from services.hardware_case_word import parse_docx
    from services.hardware_case_r1_runtime import HARDWARE_R1_STAGE_A_SCHEMA,HARDWARE_R1_STAGE_B_SCHEMA
    import jsonschema
    out.mkdir(parents=True,exist_ok=True)
    evidence_index=None
    manifest=[]
    for filename in FILES:
        path=out/filename
        make_docx(path)
        snapshot=parse_docx(path).to_snapshot()
        blocks=snapshot["structure"]["blocks"]
        assert [b["text"] for b in blocks]==list(DOC_PARAGRAPHS),blocks
        assert snapshot["identity"]["business_case_id"] == filename.split("-")[0]
        ids=[b["block_id"] for b in blocks]
        if evidence_index is None:evidence_index=ids
        else:assert ids==evidence_index
        manifest.append({"file":filename,
                         "business_case_id":snapshot["identity"]["business_case_id"],
                         "source_sha256":sha256(path.read_bytes()).hexdigest(),
                         "evidence_block_ids":ids})
    a,b=stage_a_fixture(evidence_index),stage_b_fixture(evidence_index)
    jsonschema.validate(a,HARDWARE_R1_STAGE_A_SCHEMA)
    jsonschema.validate(b,HARDWARE_R1_STAGE_B_SCHEMA)
    for x in (a,b):
        for key in ("evidence_block_ids",):
            assert str(evidence_index[0]) in str(x), "EVIDENCE_IDS_NOT_BOUND"
    (out/"stage_a_fixture.json").write_text(json.dumps(a,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (out/"stage_b_fixture.json").write_text(json.dumps(b,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    ready={
      "source_sha":"c7935e00f1f0abe828b0090524bb14ba923f3635",
      "purpose":"T2_BROWSER_ISOLATED_MOCK_ONLY",
      "formal_release":False,
      "cases":manifest,
      "stage_a_schema":"HARDWARE_R1_STAGE_A_SCHEMA",
      "stage_b_schema":"HARDWARE_R1_STAGE_B_SCHEMA",
      "port":18783,
      "model":"mock-gpt",
      "provider_calls_before_test":0,
      "real_provider":False,
      "NOTE":"A9901/A9902 are the completed G4 cases; do not reuse them.",
    }
    (out/"T2_FIXTURE_MANIFEST.json").write_text(json.dumps(ready,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return a,b,ready

def serve(product_root,out):
    sys.path.insert(0,str(product_root))
    from tools.openai_mock.server import create_server,Behavior,OpenAIMockHandler
    from services.hardware_case_markdown_agent import R1_AGENT_INPUT_VERSION,R1_REUSE_INPUT_VERSION
    class StageDispatchHandler(OpenAIMockHandler):
        _stage=None
        def _scenario_key(self):
            return self._stage or super()._scenario_key()
        def _chat_completions(self,request):
            try:
                payload=json.loads(request["messages"][-1]["content"])
                contract=payload["input_contract"]
            except (KeyError,IndexError,ValueError,TypeError):
                contract=None
            if contract==R1_AGENT_INPUT_VERSION:
                self._stage="stage-a"
            elif contract==R1_REUSE_INPUT_VERSION:
                self._stage="stage-b"
            else:
                return self._json(400,{"error":"UNEXPECTED_W3_INPUT_CONTRACT"})
            return super()._chat_completions(request)
    a,b,_=prepare(product_root,out)
    server=create_server("127.0.0.1",18783)
    server.RequestHandlerClass=StageDispatchHandler
    server.state.configure("stage-a",a,Behavior())
    server.state.configure("stage-b",b,Behavior())
    # default case is non-schema-valid and must never be used.
    server.state.configure("default",{"error":"NO_STAGE"},Behavior())
    print("MOCK_ONLY=YES\nBOUND=127.0.0.1:18783\nSTAGE_A_B=FIXTURE_BOUND\nREAL_PROVIDER=NO",flush=True)
    print("Health: http://127.0.0.1:18783/__mock__/health",flush=True)
    print("Counters: http://127.0.0.1:18783/__mock__/counters",flush=True)
    try:server.serve_forever(poll_interval=.05)
    except KeyboardInterrupt:pass
    finally:server.server_close()

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--product-root",required=True)
    p.add_argument("--out-dir",required=True)
    p.add_argument("--serve",action="store_true",help="Enable ONLY local 18783 OpenAI Mock")
    args=p.parse_args()
    product=Path(args.product_root).resolve()
    assert (product/"tools/openai_mock/server.py").is_file(),"PRODUCT_MOCK_MODULE_MISSING"
    out=Path(args.out_dir).resolve()
    if args.serve:serve(product,out)
    else:
        _,_,result=prepare(product,out)
        print("FIXTURE_GATE=PASS CASES="+str(len(result["cases"])))
        print("MODEL_BASE_URL=http://127.0.0.1:18783/v1")
        print("AGENT_POSTS=0\nPUBLISH_POSTS=0\nREAL_PROVIDER=NO")

if __name__=="__main__":
    main()
