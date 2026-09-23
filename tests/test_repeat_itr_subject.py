from __future__ import annotations

from copy import deepcopy

import pytest

from quality_knowledge.repeat_risk import (
    ITR_RESOLUTION_WORKBENCH,
    RepeatITRContractError,
    RepeatITRService,
    RepeatQueryTraceRepository,
)


class FakeITRSource:
    def __init__(self):
        self.itrs = {
            "ITR-1001": {
                "itr_id": "ITR-1001",
                "problem_description": "控制器运行中周期性重启",
                "product": "PLC",
                "version": "V2.3",
                "scene": "长稳运行",
                "existing_context": {"root_cause": "待确认", "action": "临时规避"},
                "itr_version": "ITR-V7",
            },
            "ITR-2001": {
                "itr_id": "ITR-2001",
                "problem_description": "无漏测关联问题",
                "product": "HMI",
                "version": "V1.0",
                "scene": "正常使用",
                "existing_context": {},
                "itr_version": "ITR-V1",
            },
        }
        self.related = {"ITR-1001": "MISS-9"}
        self.missed = {
            "MISS-9": {
                "missed_test_id": "MISS-9",
                "description": "异常断电场景未覆盖",
                "test_gap": "缺少掉电恢复用例",
            }
        }

    def get_itr(self, itr_ref):
        value = self.itrs.get(itr_ref)
        return deepcopy(value) if value else None

    def get_related_missed_test_ref(self, itr_ref):
        return self.related.get(itr_ref)

    def get_missed_test(self, missed_test_ref):
        value = self.missed.get(missed_test_ref)
        return deepcopy(value) if value else None


def _service(tmp_path):
    return RepeatITRService(
        FakeITRSource(),
        RepeatQueryTraceRepository(tmp_path / "repeat-risk.sqlite3"),
        algorithm_version="repeat-risk/test-v1",
    )


def test_itr_subject_contains_required_workbench_snapshot(tmp_path):
    service = _service(tmp_path)
    inspected = service.inspect_subject("ITR-1001")

    subject = inspected["subject"]
    assert subject["itr_ref"] == "ITR-1001"
    assert subject["source"] == ITR_RESOLUTION_WORKBENCH
    assert subject["itr_snapshot"] == {
        "itr_id": "ITR-1001",
        "problem_description": "控制器运行中周期性重启",
        "product": "PLC",
        "version": "V2.3",
        "scene": "长稳运行",
        "existing_context": {"root_cause": "待确认", "action": "临时规避"},
        "itr_version": "ITR-V7",
        "source": ITR_RESOLUTION_WORKBENCH,
    }
    assert inspected["optional_context"] == {
        "missed_test_available": True,
        "missed_test_ref": "MISS-9",
    }


def test_missed_test_is_not_mixed_in_unless_user_selects_it(tmp_path):
    service = _service(tmp_path)
    result = service.create_query("ITR-1001", include_missed_test=False, correlation_id="CORR-1")

    assert result["optional_context"] is None
    trace = result["trace"]
    assert trace["subject_ref"] == "ITR-1001"
    assert trace["include_missed_test"] is False
    assert trace["missed_test_ref"] is None
    assert trace["optional_context"] is None
    assert trace["correlation_id"] == "CORR-1"


def test_selected_related_missed_test_is_snapshotted_as_optional_context(tmp_path):
    service = _service(tmp_path)
    result = service.create_query("ITR-1001", include_missed_test=True)

    assert result["subject"]["itr_ref"] == "ITR-1001"
    assert result["optional_context"]["missed_test_ref"] == "MISS-9"
    assert result["optional_context"]["snapshot"]["test_gap"] == "缺少掉电恢复用例"
    assert result["trace"]["include_missed_test"] is True


def test_no_missed_test_does_not_block_repeat_query(tmp_path):
    service = _service(tmp_path)
    result = service.create_query("ITR-2001")

    assert result["query_id"].startswith("RQ-")
    assert result["trace"]["include_missed_test"] is False
    assert result["optional_context"] is None


def test_unrelated_missed_test_cannot_be_selected(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(RepeatITRContractError) as error:
        service.create_query(
            "ITR-1001",
            include_missed_test=True,
            missed_test_ref="MISS-OTHER",
        )

    assert error.value.code == "MISSED_TEST_NOT_RELATED"


def test_query_trace_is_immutable_snapshot_not_second_itr_master(tmp_path):
    source = FakeITRSource()
    repository = RepeatQueryTraceRepository(tmp_path / "repeat-risk.sqlite3")
    service = RepeatITRService(source, repository, algorithm_version="repeat-risk/test-v1")

    result = service.create_query("ITR-1001", include_missed_test=True, correlation_id="CORR-X")
    query_id = result["query_id"]

    source.itrs["ITR-1001"]["problem_description"] = "后来主数据已变化"
    source.missed["MISS-9"]["description"] = "后来漏测问题已变化"

    restored = repository.get(query_id)
    assert restored["itr_snapshot"]["problem_description"] == "控制器运行中周期性重启"
    assert restored["optional_context"]["snapshot"]["description"] == "异常断电场景未覆盖"
    assert restored["algorithm_version"] == "repeat-risk/test-v1"
    assert restored["correlation_id"] == "CORR-X"


def test_missing_itr_fails_closed(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(RepeatITRContractError) as error:
        service.create_query("ITR-NOT-FOUND")

    assert error.value.code == "ITR_NOT_FOUND"
