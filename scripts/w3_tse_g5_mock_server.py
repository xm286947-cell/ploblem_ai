"""W3 G5 browser-only failure injection fixture server.

Uses frozen OpenAI Mock; only separate synthetic A9915 fails Stage B.
No changes to product Provider/Runtime/Prompt/Schema.
"""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import sys

def main():
    product=Path(sys.argv[1]).resolve()
    fixtures=Path(sys.argv[2]).resolve()
    sys.path.insert(0,str(product))
    import jsonschema
    from services.hardware_case_word import parse_docx
    from services.hardware_case_r1_runtime import HARDWARE_R1_STAGE_A_SCHEMA,HARDWARE_R1_STAGE_B_SCHEMA
    from services.hardware_case_markdown_agent import R1_AGENT_INPUT_VERSION,R1_REUSE_INPUT_VERSION
    from tools.openai_mock.server import create_server,OpenAIMockHandler,Behavior
    helper=fixtures/"w3_tse_browser_mock_fixtures.py"
    spec=importlib.util.spec_from_file_location("w3_test_kit",helper)
    module=importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    first_name="A9915-G5阶段B故障注入.docx"
    second_name="A9916-G5正常案例.docx"
    marker="[G5_STAGE_B_EXPECTED_HTTP_503]"
    original=module.DOC_PARAGRAPHS
    altered=(original[0]+" "+marker,*original[1:])
    module.DOC_PARAGRAPHS=altered
    first=fixtures/first_name
    module.make_docx(first)
    first_snap=parse_docx(first).to_snapshot()
    first_blocks=first_snap["structure"]["blocks"]
    assert marker in first_blocks[0]["text"]
    failing_a=module.stage_a_fixture([x["block_id"] for x in first_blocks])
    module.DOC_PARAGRAPHS=original
    second=fixtures/second_name
    module.make_docx(second)
    second_snap=parse_docx(second).to_snapshot()
    normal_a=module.stage_a_fixture([x["block_id"] for x in second_snap["structure"]["blocks"]])
    normal_b=module.stage_b_fixture([x["block_id"] for x in second_snap["structure"]["blocks"]])
    for a in (failing_a,normal_a):jsonschema.validate(a,HARDWARE_R1_STAGE_A_SCHEMA)
    jsonschema.validate(normal_b,HARDWARE_R1_STAGE_B_SCHEMA)
    assert first_snap["identity"]["business_case_id"]=="A9915"
    assert second_snap["identity"]["business_case_id"]=="A9916"
    manifest={
        "frozen_source":"c7935e00f1f0abe828b0090524bb14ba923f3635",
        "mode":"T2_G5_TEST_ONLY",
        "cases":[{"case":"A9915","file":first_name,"expected":"STAGE_B_FAILED"},
                 {"case":"A9916","file":second_name,"expected":"CANDIDATE_READY_OR_REVIEW"}],
        "marker":marker,"real_provider":False,"formal_publish":False,
    }
    (fixtures/"G5_FAIL_INJECTION_MANIFEST.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    class Handler(OpenAIMockHandler):
        stage=None
        def _scenario_key(self):
            return self.stage or super()._scenario_key()
        def _chat_completions(self,request):
            try:
                user=json.loads(request["messages"][-1]["content"])
                contract=user["input_contract"]
            except (KeyError,IndexError,ValueError,TypeError):
                contract=None
            if contract==R1_AGENT_INPUT_VERSION:
                identity=user.get("source_fact") or {}
                self.stage="stage-a-failcase" if identity.get("business_case_id")=="A9915" else "stage-a-good"
            elif contract==R1_REUSE_INPUT_VERSION:
                blocks=user.get("evidence_blocks") or []
                has_marker=any(marker in (block.get("text") or "") for block in blocks)
                self.stage="stage-b-failcase" if has_marker else "stage-b-good"
            else:
                return self._json(400,{"error":"MOCK_STAGE_CONTRACT_UNKNOWN"})
            return super()._chat_completions(request)
    server=create_server("127.0.0.1",18783)
    server.RequestHandlerClass=Handler
    server.state.configure("stage-a-failcase",failing_a,Behavior())
    server.state.configure("stage-a-good",normal_a,Behavior())
    server.state.configure("stage-b-good",normal_b,Behavior())
    server.state.configure("stage-b-failcase",normal_b,Behavior.from_dict({"status":503}))
    server.state.configure("default",{"error":"INVALID_CONTRACT"},Behavior())
    print("MOCK_MODE=G5_STAGED_FAIL_INJECTION\nMOCK_LISTEN=127.0.0.1:18783\nREAL_PROVIDER=NO",flush=True)
    try:server.serve_forever(poll_interval=.05)
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=="__main__":
    main()
