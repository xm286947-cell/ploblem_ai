from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from zipfile import ZipFile

import pytest

from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from quality_knowledge.major_cases.runtime_provider import (
    MajorD01ProviderObject,
    MajorD01RuntimeService,
)
from quality_knowledge.major_cases.service import MajorCaseService
from quality_knowledge.runtime_model_config import (
    resolve_major_runtime_model_config,
)
from runtime import (
    AgentConfigLoader,
    ConfiguredAgentRuntime,
    RuntimeStatus,
    SqliteTaskStore,
)


ROOT = Path(__file__).resolve().parents[1]
TRUNCATION_AGENT_CONFIG = (
    ROOT
    / "config/runtime/agents/major_issue.d01.truncation_golden.yaml"
)
PRODUCTION_AGENT_CONFIG = (
    ROOT
    / "config/runtime/agents/major_issue.d01.extract.yaml"
)


def _require_real_provider() -> Path:
    enabled = os.environ.get(
        "MAJOR_D01_TRUNCATION_REAL_E2E",
        "",
    ).strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        pytest.skip("MAJOR-D01-REAL-TRUNCATION-GOLDEN opt-in disabled")

    try:
        return resolve_major_runtime_model_config(ROOT)
    except ValueError as exc:
        pytest.fail(str(exc))


def _docx(path: Path) -> Path:
    sections = [
        (
            "问题经过",
            "设备在产线正常运行期间执行配置保存操作。"
            "保存过程发生瞬时掉电，设备重启后发现配置文件无法解析，"
            "应用启动失败并进入异常恢复流程。现场重复操作后可以稳定复现。"
        ),
        (
            "根因分析",
            "配置文件采用直接覆盖写入，写入过程中旧文件已经被修改，"
            "新内容尚未完整落盘。掉电发生在该窗口时会形成半写入文件，"
            "启动阶段缺少双副本或事务式恢复，因此损坏文件直接导致加载失败。"
        ),
        (
            "整改措施",
            "配置保存改为临时文件完整写入并校验成功后执行原子替换，"
            "同时保留最近一个有效版本作为恢复副本，增加写入状态标记和异常恢复逻辑，"
            "并在关键保存路径统一接入该机制。"
        ),
        (
            "验证结果",
            "完成连续掉电恢复验证与长稳运行验证。测试覆盖保存前、保存中、"
            "原子替换前后多个时间窗口，连续执行多轮掉电后均可正常启动，"
            "配置数据保持完整，未再次出现文件损坏和启动失败。"
        ),
    ]
    paragraphs: list[str] = []
    for heading, body in sections:
        paragraphs.append(
            f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
            f'<w:r><w:t>{heading}</w:t></w:r></w:p>'
        )
        paragraphs.append(
            f"<w:p><w:r><w:t>{body}</w:t></w:r></w:p>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w='
        '"http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(paragraphs)
        + "</w:body></w:document>"
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return path


def _raw_database_dump(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return "\n".join(connection.iterdump())



def test_truncation_golden_config_is_isolated_from_production() -> None:
    loader = AgentConfigLoader(
        root=ROOT,
        model_profiles=ROOT / "config/runtime/model.yaml",
        schemas={"MajorD01ProviderObject": MajorD01ProviderObject},
        environ={
            "DASHSCOPE_BASE_URL": "http://127.0.0.1:9/v1",
            "DASHSCOPE_API_KEY": "TEST_ONLY",
        },
    )

    production = loader.load(PRODUCTION_AGENT_CONFIG)
    truncation = loader.load(TRUNCATION_AGENT_CONFIG)

    assert production.definition.agent_id == "major_issue.d01.extract"
    assert truncation.definition.agent_id == production.definition.agent_id
    assert production.execution_policy.model_policy["max_tokens"] == 8192
    assert truncation.execution_policy.model_policy["max_tokens"] == 192
    assert truncation.execution_policy.retry_budget.validation_attempts == 1
    assert truncation.execution_policy.retry_budget.max_provider_calls_per_step == 1
    assert truncation.definition.metadata["acceptance_only"] is True
    assert (
        truncation.definition.metadata["expected_signal"]
        == "finish_reason_length"
    )

def test_major_d01_real_provider_truncation_recovers_by_replanning(
    tmp_path: Path,
) -> None:
    model_config = _require_real_provider()

    repository = MajorKnowledgeRepository(
        tmp_path / "knowledge.sqlite3",
        tmp_path / "attachments",
    )
    case_service = MajorCaseService(repository)
    case = case_service.create_case(
        "D01真实截断恢复Golden",
        "G1",
        "SOFTWARE",
    )
    ingest = case_service.ingest(
        case["case_id"],
        _docx(tmp_path / "review-truncation.docx"),
        current_itrs=["ITR2026092205"],
    )
    version_id = ingest["version_id"]
    event = repository.events(case["case_id"])[0]

    loader = AgentConfigLoader(
        root=ROOT,
        model_profiles=model_config,
        schemas={"MajorD01ProviderObject": MajorD01ProviderObject},
        environ=os.environ,
    )
    runtime_db = tmp_path / "runtime-truncation.sqlite3"
    store = SqliteTaskStore(runtime_db)
    runtime = ConfiguredAgentRuntime(
        store,
        config_loader=loader,
    )

    service = MajorD01RuntimeService(
        repository,
        runtime,
        store,
        agent_config_path=TRUNCATION_AGENT_CONFIG,
        max_provider_calls=12,
        initial_max_units_per_chunk=4,
        initial_max_payload_chars=100000,
    )

    result = service.execute(
        case_id=case["case_id"],
        version_id=version_id,
        event_id=event["event_id"],
        skill_version_id="real-truncation-golden-v1",
    )

    outcome = result["outcome"]

    assert outcome["status"] == RuntimeStatus.COMPLETED.value
    assert outcome["replans"] >= 1
    assert outcome["truncated_task_ids"]
    assert len(outcome["task_ids"]) >= 2
    assert outcome["provider_calls"] >= 3
    assert outcome["gate"]["passed"] is True

    committed = outcome["committed_objects"]
    assert {
        item["object_id"]
        for item in committed
    } == {
        "ISSUE_FACT",
        "ROOT_CAUSE",
        "ACTION",
        "VERIFICATION",
    }

    partials = outcome["committed_partials"]
    assert len(partials) >= 2
    covered_units = {
        unit_id
        for partial in partials
        for unit_id in partial["unit_ids"]
    }
    assert len(covered_units) >= 4

    entries = repository.entries_for_event(event["event_id"])
    assert {item["entry_type"] for item in entries} == {
        "ISSUE_FACT",
        "ROOT_CAUSE",
        "ACTION",
        "VERIFICATION",
    }
    assert all(item["evidence"] for item in entries)
    assert all(
        item["analysis_metadata"]["runtime_replans"] >= 1
        for item in entries
    )
    assert all(
        item["analysis_metadata"]["runtime_truncated_task_ids"]
        for item in entries
    )

    # Extraction is technically complete, but Human Review remains mandatory.
    assert result["business_gate"]["reasons"] == [
        "HUMAN_REVIEW_INCOMPLETE"
    ]
    assert result["business_consumable"] is False

    resolved = loader.load(TRUNCATION_AGENT_CONFIG)
    assert resolved.execution_policy.model_policy["max_tokens"] == 192

    secret = loader.get_runtime_api_key(
        resolved.config_hash,
        api_key_env=resolved.provider.api_key_env,
    )
    if secret:
        assert secret not in _raw_database_dump(runtime_db)
