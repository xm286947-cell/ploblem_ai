"""TSE-only Stage B single-case failure injector for W3 G5 Chrome.

Runs existing OpenAI Mock on loopback and dispatches by Stage A case identity,
then Stage B's evidence-grounded subject. Never modifies product Runtime.
"""
from __future__ import annotations
import argparse,copy,json,sys
from pathlib import Path

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--product-root",required=True)
    parser.add_argument("--out-dir",required=True)
    args=parser.parse_args()
    product=Path(args.product_root).resolve()
    sys.path.insert(0,str(product))
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from w3_tse_browser_mock_fixtures import prepare
    from tools.openai_mock.server import create_server,OpenAIMockHandler,Behavior
    from services.hardware_case_markdown_agent import R1_AGENT_INPUT_VERSION,R1_REUSE_INPUT_VERSION
    a,b,_=prepare(product,Path(args.out_dir).resolve())
    a_bad=copy.deepcopy(a)
    a_bad["engineering_context"]["primary_subject"]["value"]="串口屏"
    class Handler(OpenAIMockHandler):
        _selected=None
        def _scenario_key(self):
            return self._selected or super()._scenario_key()
        def _chat_completions(self,request):
            try:
                user=json.loads(request["messages"][-1]["content"])
                contract=user["input_contract"]
            except (KeyError,IndexError,ValueError,TypeError):
                return self._json(400,{"error":"UNEXPECTED_W3_G5_REQUEST"})
            if contract==R1_AGENT_INPUT_VERSION:
                case_id=str((user.get("source_fact") or {}).get("business_case_id") or "")
                if case_id=="A9905":self._selected="stage-a"
                elif case_id=="A9906":self._selected="stage-a-alt"
                else:return self._json(400,{"error":"UNEXPECTED_G5_CASE"})
            elif contract==R1_REUSE_INPUT_VERSION:
                subject=((user.get("engineering_context") or {}).get("primary_subject") or {}).get("value")
                if subject=="MCU":self._selected="stage-b"
                elif subject=="串口屏":self._selected="stage-b-fail"
                else:return self._json(400,{"error":"UNEXPECTED_G5_STAGE_B_SUBJECT"})
            else:return self._json(400,{"error":"UNEXPECTED_G5_CONTRACT"})
            return super()._chat_completions(request)
    server=create_server("127.0.0.1",18783)
    server.RequestHandlerClass=Handler
    server.state.configure("stage-a",a,Behavior())
    server.state.configure("stage-a-alt",a_bad,Behavior())
    server.state.configure("stage-b",b,Behavior())
    server.state.configure("stage-b-fail",b,Behavior.from_dict({"status":503}))
    server.state.configure("default","NOT_CONFIGURED",Behavior())
    print("G5_LOCAL_FAIL_INJECTOR=READY\nCASE_A9905=STAGE_B_PASS\nCASE_A9906=STAGE_B_HTTP_503\nREAL_PROVIDER=NO",flush=True)
    try:server.serve_forever(poll_interval=.03)
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=="__main__":main()
