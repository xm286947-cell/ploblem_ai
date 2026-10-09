"""S2 Unified Runtime + Mock Provider + verified source references."""
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.request import Request, urlopen

from services.hardware_engineering_runtime import HardwareEngineeringRuntimeInvoker
from services.hardware_engineering_analysis import HardwareEngineeringAnalysisService
from tools.openai_mock.server import create_server

ROOT = Path(__file__).resolve().parents[1]


class Formal:
    def get(self, knowledge_id):
        return {
            "knowledge_id": "KO-0207", "business_case_id": "A0207",
            "engineering_rule": "模拟量输出精度强依赖参考源电压精度",
            "evidence_refs": ["EV-0207"],
        } if knowledge_id == "KO-0207" else None


@contextmanager
def mock_server():
    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_grounded_consumption_with_real_unified_runtime_mock(tmp_path):
    with mock_server() as (host, port):
        body = json.dumps({"scenario_key": "default", "payload": {
            "summary": "参考源精度影响模拟量输出", "checks": [
                {"recommendation": "设计中应检查参考源电压精度",
                 "source_field": "engineering_rule",
                 "source_excerpt": "模拟量输出精度强依赖参考源电压精度",
                 "evidence_id": "EV-0207"},
            ], "unknowns": ["具体设计余量未给出"],
        }, "behavior": {}}, ensure_ascii=False).encode()
        with urlopen(Request(f"http://{host}:{port}/__mock__/scenario", data=body,
                             method="POST", headers={"Content-Type":"application/json"}), timeout=2) as resp:
            assert resp.status == 200
        cfg = tmp_path / "model.local.yaml"
        cfg.write_text(
            f"active_model: hardware_consume_mock\nmodels:\n"
            f"  hardware_consume_mock:\n"
            f"    provider: openai_compatible\n"
            f"    base_url: http://{host}:{port}/v1\n"
            f"    api_key: TRACE_SECRET_NOT_TO_STORE\n"
            f"    model: hardware-consume-mock\n"
            f"    temperature: 0\n"
            f"    max_tokens: 1200\n", encoding="utf-8")
        db = tmp_path / "agent.db"
        agent = HardwareEngineeringRuntimeInvoker(
            root=ROOT,
            environ={"HARDWARE_CASE_MODEL_CONFIG": str(cfg),
                     "HARDWARE_ENGINEERING_RUNTIME_DB": str(db)},
        )
        response = HardwareEngineeringAnalysisService(Formal(), agent).analyze(
            "KO-0207", "DESIGN_REUSE")
        assert response["checks"][0]["source_field"] == "engineering_rule"
        assert response["trace"]["provider_calls"] == 1
        assert response["trace"]["model"] == "hardware-consume-mock"
        assert b"TRACE_SECRET_NOT_TO_STORE" not in db.read_bytes()
