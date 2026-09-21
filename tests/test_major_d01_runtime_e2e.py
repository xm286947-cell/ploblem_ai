from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
from typing import Iterator
from urllib.request import Request, urlopen
from zipfile import ZipFile

from runtime import AgentConfigLoader, ConfiguredAgentRuntime, RuntimeStatus, SqliteTaskStore
from tools.openai_mock.server import create_server
from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from quality_knowledge.major_cases.service import MajorCaseService
from quality_knowledge.major_cases.runtime_provider import (
    MajorD01ProviderObject,
    MajorD01RuntimeService,
)


ROOT = Path(__file__).resolve().parents[1]
AGENT_CONFIG = ROOT / "config/runtime/agents/major_issue.d01.extract.yaml"
SECRET = "MAJOR_D01_SECRET_MUST_NOT_PERSIST"


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


def configure(host: str, port: int, payload, behavior=None) -> None:
    raw = json.dumps(
        {
            "scenario_key": "default",
            "payload": payload,
            "behavior": behavior or {},
        },
        ensure_ascii=False,
    ).encode()
    req = Request(
        f"http://{host}:{port}/__mock__/scenario",
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req, timeout=2) as response:
        assert response.status == 200


def counters(host: str, port: int) -> dict[str, int]:
    with urlopen(f"http://{host}:{port}/__mock__/counters", timeout=2) as response:
        return json.loads(response.read().decode())["data"]


def _docx(path: Path) -> Path:
    sections = [
        ("问题经过", "设备运行过程中掉电后配置损坏，重启后无法恢复。"),
        ("根因分析", "配置文件写入不是原子操作，掉电窗口造成半写入状态。"),
        ("整改措施", "采用临时文件加原子替换，并补充掉电保护。"),
        ("验证结果", "连续执行掉电恢复验证72小时，问题未再复现。"),
    ]
    paragraphs = []
    for heading, body in sections:
        paragraphs.append(
            f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
            f'<w:r><w:t>{heading}</w:t></w:r></w:p>'
        )
        paragraphs.append(f"<w:p><w:r><w:t>{body}</w:t></w:r></w:p>")
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>" + "".join(paragraphs) + "</w:body></w:document>"
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return path


def _runtime(tmp_path: Path, base_url: str):
    loader = AgentConfigLoader(
        root=ROOT,
        model_profiles=ROOT / "config/runtime/model.yaml",
        schemas={"MajorD01ProviderObject": MajorD01ProviderObject},
        environ={
            "DASHSCOPE_BASE_URL": base_url,
            "DASHSCOPE_API_KEY": SECRET,
        },
    )
    store = SqliteTaskStore(tmp_path / "runtime.db")
    runtime = ConfiguredAgentRuntime(store, config_loader=loader)
    return store, runtime


def _raw_database_dump(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return "\n".join(connection.iterdump())


def _payload_for_fragments(fragments: list[dict]) -> list[dict]:
    assert len(fragments) >= 4
    return [
        {
            "object_id": "ISSUE_FACT",
            "content": "运行中掉电后配置损坏且重启无法恢复。",
            "fragment_ids": [fragments[0]["fragment_id"]],
            "confidence": 0.93,
            "explanation": "问题经过片段直接描述故障现象。",
            "mechanism": "",
        },
        {
            "object_id": "ROOT_CAUSE",
            "content": "配置写入非原子，掉电窗口形成半写入状态。",
            "fragment_ids": [fragments[1]["fragment_id"]],
            "confidence": 0.91,
            "explanation": "根因分析片段给出直接因果关系。",
            "mechanism": "非原子写入在掉电时无法保证旧/新版本二选一完整落盘。",
        },
        {
            "object_id": "ACTION",
            "content": "采用临时文件加原子替换并补充掉电保护。",
            "fragment_ids": [fragments[2]["fragment_id"]],
            "confidence": 0.9,
            "explanation": "整改措施片段直接给出技术措施。",
            "mechanism": "",
        },
        {
            "object_id": "VERIFICATION",
            "content": "连续掉电恢复验证72小时未再复现。",
            "fragment_ids": [fragments[3]["fragment_id"]],
            "confidence": 0.89,
            "explanation": "验证结果片段直接给出验证结论。",
            "mechanism": "",
        },
    ]


def test_major_d01_configured_runtime_provider_end_to_end(tmp_path: Path) -> None:
    with running_server() as (host, port):
        base_url = f"http://{host}:{port}/v1"

        repo = MajorKnowledgeRepository(
            tmp_path / "knowledge.sqlite3",
            tmp_path / "attachments",
        )
        case_service = MajorCaseService(repo)
        case = case_service.create_case("D01真实链路模拟验收", "G1", "SOFTWARE")
        ingest = case_service.ingest(
            case["case_id"],
            _docx(tmp_path / "review.docx"),
            current_itrs=["ITR2026092101"],
        )
        version_id = ingest["version_id"]
        event = repo.events(case["case_id"])[0]
        fragments = repo.fragments(version_id)
        assert len(fragments) >= 4
        configure(host, port, _payload_for_fragments(fragments))

        store, runtime = _runtime(tmp_path, base_url)
        service = MajorD01RuntimeService(
            repo,
            runtime,
            store,
            agent_config_path=AGENT_CONFIG,
        )

        result = service.execute(
            case_id=case["case_id"],
            version_id=version_id,
            event_id=event["event_id"],
        )

        outcome = result["outcome"]
        assert outcome["status"] == RuntimeStatus.COMPLETED.value
        assert outcome["provider_calls"] == 1
        assert len(outcome["committed_objects"]) == 4
        assert counters(host, port)["default"] == 1
        assert result["business_consumable"] is False
        assert result["business_gate"]["reasons"] == ["HUMAN_REVIEW_INCOMPLETE"]

        entries = repo.entries_for_event(event["event_id"])
        assert {item["entry_type"] for item in entries} == {
            "ISSUE_FACT",
            "ROOT_CAUSE",
            "ACTION",
            "VERIFICATION",
        }
        assert {item["status"] for item in entries} == {"PENDING"}
        assert all(item["evidence"] for item in entries)
        assert next(
            item for item in entries if item["entry_type"] == "ROOT_CAUSE"
        )["mechanism"]

        runs = store.list_runs(outcome["task_id"])
        attempt = store.list_attempts(
            store.list_step_runs(runs[0].run_id)[0].step_run_id
        )[0]
        assert attempt.provider == "openai_compatible"
        assert attempt.model_name == "qwen3.8-max"
        assert attempt.provider_call_seq == 1
        assert SECRET not in _raw_database_dump(store.db_path)


def test_major_d01_rejects_unknown_evidence_fragment_under_runtime_retry(tmp_path: Path) -> None:
    with running_server() as (host, port):
        base_url = f"http://{host}:{port}/v1"

        repo = MajorKnowledgeRepository(
            tmp_path / "knowledge.sqlite3",
            tmp_path / "attachments",
        )
        case_service = MajorCaseService(repo)
        case = case_service.create_case("D01证据绑定失败", "G1", "SOFTWARE")
        ingest = case_service.ingest(
            case["case_id"],
            _docx(tmp_path / "review.docx"),
            current_itrs=["ITR2026092102"],
        )
        version_id = ingest["version_id"]
        event = repo.events(case["case_id"])[0]
        fragments = repo.fragments(version_id)

        invalid = _payload_for_fragments(fragments)
        invalid[0]["fragment_ids"] = ["UNKNOWN-FRAGMENT"]
        configure(host, port, invalid)

        store, runtime = _runtime(tmp_path, base_url)
        service = MajorD01RuntimeService(
            repo,
            runtime,
            store,
            agent_config_path=AGENT_CONFIG,
        )
        result = service.execute(
            case_id=case["case_id"],
            version_id=version_id,
            event_id=event["event_id"],
        )

        outcome = result["outcome"]
        assert outcome["status"] == RuntimeStatus.PARTIAL.value
        assert outcome["provider_calls"] == 2
        assert counters(host, port)["default"] == 2
        assert result["persisted_entry_ids"] == []
        assert repo.entries_for_event(event["event_id"]) == []


def test_major_d01_external_model_config_without_provider_env(tmp_path: Path) -> None:
    with running_server() as (host, port):
        base_url = f"http://{host}:{port}/v1"
        model_config = tmp_path / "model.local.yaml"
        model_config.write_text(
            f"""
active_model: qwen_prod
models:
  qwen_prod:
    provider: openai_compatible
    base_url: {base_url}
    api_key: LOCAL_RUNTIME_SECRET
    model: qwen3.8-max
    temperature: 0
    max_tokens: 8192
""".strip(),
            encoding="utf-8",
        )

        repo = MajorKnowledgeRepository(
            tmp_path / "knowledge.sqlite3",
            tmp_path / "attachments",
        )
        case_service = MajorCaseService(repo)
        case = case_service.create_case("D01外部模型配置", "G1", "SOFTWARE")
        ingest = case_service.ingest(
            case["case_id"],
            _docx(tmp_path / "review-local.docx"),
            current_itrs=["ITR2026092203"],
        )
        version_id = ingest["version_id"]
        event = repo.events(case["case_id"])[0]
        fragments = repo.fragments(version_id)
        configure(host, port, _payload_for_fragments(fragments))

        loader = AgentConfigLoader(
            root=ROOT,
            model_profiles=model_config,
            schemas={"MajorD01ProviderObject": MajorD01ProviderObject},
            environ={},
        )
        store = SqliteTaskStore(tmp_path / "runtime-local.db")
        runtime = ConfiguredAgentRuntime(store, config_loader=loader)
        service = MajorD01RuntimeService(
            repo,
            runtime,
            store,
            agent_config_path=AGENT_CONFIG,
        )

        result = service.execute(
            case_id=case["case_id"],
            version_id=version_id,
            event_id=event["event_id"],
        )

        assert result["outcome"]["status"] == RuntimeStatus.COMPLETED.value
        assert result["outcome"]["provider_calls"] == 1
        assert counters(host, port)["default"] == 1
        assert "LOCAL_RUNTIME_SECRET" not in _raw_database_dump(store.db_path)
