from __future__ import annotations

import os
from pathlib import Path
from zipfile import ZipFile

import pytest

from runtime import AgentConfigLoader, ConfiguredAgentRuntime, RuntimeStatus, SqliteTaskStore
from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from quality_knowledge.major_cases.service import MajorCaseService
from quality_knowledge.major_cases.runtime_provider import (
    MajorD01ProviderObject,
    MajorD01RuntimeService,
)


ROOT = Path(__file__).resolve().parents[1]
AGENT_CONFIG = ROOT / "config/runtime/agents/major_issue.d01.extract.yaml"
DEFAULT_LOCAL_MODEL_CONFIG = ROOT / "config/model.local.yaml"
DEFAULT_RUNTIME_MODEL_CONFIG = ROOT / "config/runtime/model.yaml"


def _model_config_path() -> Path:
    configured = os.environ.get("MAJOR_MODEL_CONFIG", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = path.resolve()
        if not path.is_file():
            pytest.fail(f"MAJOR_MODEL_CONFIG does not exist: {path}")
        return path

    if DEFAULT_LOCAL_MODEL_CONFIG.is_file():
        return DEFAULT_LOCAL_MODEL_CONFIG

    return DEFAULT_RUNTIME_MODEL_CONFIG


def _require_real_provider() -> None:
    enabled = os.environ.get("MAJOR_REAL_E2E", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        pytest.skip("MAJOR-D01-E2E-001 opt-in disabled")

    model_config = _model_config_path()
    if model_config == DEFAULT_LOCAL_MODEL_CONFIG:
        return

    # Runtime's shared production profile is allowed to resolve via env refs.
    # The test does not require env when a local model config is supplied.
    if os.environ.get("MAJOR_MODEL_CONFIG", "").strip():
        return

    missing = [
        name
        for name in ("DASHSCOPE_BASE_URL", "DASHSCOPE_API_KEY")
        if not os.environ.get(name)
    ]
    if missing:
        pytest.skip(
            "NOT_RUN_NO_RUNTIME_MODEL_CONFIG: "
            + ",".join(missing)
            + "; provide config/model.local.yaml or MAJOR_MODEL_CONFIG"
        )


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


def test_major_d01_real_provider_uses_runtime_model_config(tmp_path: Path) -> None:
    _require_real_provider()

    repo = MajorKnowledgeRepository(
        tmp_path / "knowledge.sqlite3",
        tmp_path / "attachments",
    )
    case_service = MajorCaseService(repo)
    case = case_service.create_case("D01真实Provider验收", "G1", "SOFTWARE")
    ingest = case_service.ingest(
        case["case_id"],
        _docx(tmp_path / "review.docx"),
        current_itrs=["ITR2026092201"],
    )
    version_id = ingest["version_id"]
    event = repo.events(case["case_id"])[0]

    loader = AgentConfigLoader(
        root=ROOT,
        model_profiles=_model_config_path(),
        schemas={"MajorD01ProviderObject": MajorD01ProviderObject},
        environ=os.environ,
    )
    store = SqliteTaskStore(tmp_path / "runtime.db")
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

    outcome = result["outcome"]
    assert outcome["status"] == RuntimeStatus.COMPLETED.value
    assert 1 <= outcome["provider_calls"] <= 4
    assert {
        item["object_id"] for item in outcome["committed_objects"]
    } == {
        "ISSUE_FACT",
        "ROOT_CAUSE",
        "ACTION",
        "VERIFICATION",
    }

    entries = repo.entries_for_event(event["event_id"])
    assert {item["entry_type"] for item in entries} == {
        "ISSUE_FACT",
        "ROOT_CAUSE",
        "ACTION",
        "VERIFICATION",
    }
    assert all(item["origin"] == "AI" for item in entries)
    assert all(item["evidence"] for item in entries)
    assert result["business_gate"]["reasons"] == ["HUMAN_REVIEW_INCOMPLETE"]
    assert result["business_consumable"] is False
