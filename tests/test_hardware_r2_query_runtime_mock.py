"""Unified Runtime mock smoke. Not a real-provider acceptance."""
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.request import Request, urlopen

from services.hardware_query_runtime import HardwareQueryRuntimeInvoker
from tools.openai_mock.server import create_server

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def mock_server():
    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_query_runtime_mock_returns_actual_task_run_provider_trace(tmp_path):
    with mock_server() as (host, port):
        raw = json.dumps({"scenario_key": "default",
                          "payload": {"intent": "DESIGN_REUSE", "search_terms": ["模拟量"]},
                          "behavior": {}}).encode()
        request = Request(f"http://{host}:{port}/__mock__/scenario",
                          data=raw, method="POST", headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=2) as response:
            assert response.status == 200
        cfg = tmp_path / "model.local.yaml"
        cfg.write_text(
            f"active_model: hardware_query_mock\nmodels:\n"
            f"  hardware_query_mock:\n"
            f"    provider: openai_compatible\n"
            f"    base_url: http://{host}:{port}/v1\n"
            f"    api_key: TEST_SECRET_DO_NOT_STORE\n"
            f"    model: hardware-query-mock\n"
            f"    temperature: 0\n"
            f"    max_tokens: 600\n",
            encoding="utf-8",
        )
        db = tmp_path / "query-runtime.db"
        invoker = HardwareQueryRuntimeInvoker(root=ROOT, environ={
            "HARDWARE_CASE_MODEL_CONFIG": str(cfg),
            "HARDWARE_QUERY_RUNTIME_DB": str(db),
        })
        result = invoker("设计模拟量电路时，有什么经验可以借鉴？")
        assert result["intent"] == "DESIGN_REUSE"
        assert result["search_terms"] == ["模拟量"]
        assert result["trace"]["task_id"]
        assert result["trace"]["run_id"]
        assert result["trace"]["provider_calls"] == 1
        assert result["trace"]["model"] == "hardware-query-mock"
        assert b"TEST_SECRET_DO_NOT_STORE" not in db.read_bytes()
