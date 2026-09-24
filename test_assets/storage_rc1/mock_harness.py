from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tools.openai_mock.server import create_server


FIXTURE_DIR = Path(__file__).with_name("fixtures")
AUTH_HEADER = {"Authorization": "Bearer STORAGE_RC1_TEST_SECRET"}


def fixture_files() -> list[Path]:
    return sorted(FIXTURE_DIR.glob("M*.json"))


def load_fixture(mock_id: str) -> dict[str, Any]:
    matches = [path for path in fixture_files() if path.name.startswith(f"{mock_id}_")]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one fixture for {mock_id}, got {len(matches)}")
    return json.loads(matches[0].read_text(encoding="utf-8"))


def load_all_fixtures() -> list[dict[str, Any]]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in fixture_files()]


@contextmanager
def running_server() -> Iterator[tuple[str, int]]:
    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def configure_fixture(host: str, port: int, fixture: dict[str, Any]) -> None:
    body = json.dumps(
        {
            "scenario_key": fixture["mock_id"],
            "payload": fixture.get("payload"),
            "behavior": fixture.get("behavior") or {},
        }
    ).encode("utf-8")
    request = Request(
        f"http://{host}:{port}/__mock__/scenario",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=5) as response:
        assert response.status == 200


def chat_completion(
    host: str,
    port: int,
    mock_id: str,
    *,
    timeout: float = 5.0,
) -> tuple[int, bytes]:
    request_body = json.dumps(
        {
            "model": "mock-gpt",
            "messages": [{"role": "user", "content": "storage rc1 test"}],
            "stream": False,
        }
    ).encode("utf-8")
    request = Request(
        f"http://{host}:{port}/v1/chat/completions",
        data=request_body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": AUTH_HEADER["Authorization"],
            "X-Mock-Scenario-Key": mock_id,
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()


def requests_for(host: str, port: int, mock_id: str) -> list[dict[str, Any]]:
    with urlopen(
        f"http://{host}:{port}/__mock__/requests?scenario_key={mock_id}",
        timeout=2,
    ) as response:
        return json.loads(response.read().decode("utf-8"))["data"]
