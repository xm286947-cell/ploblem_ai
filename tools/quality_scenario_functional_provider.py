#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


EVIDENCE_ID = "bundle.analysis.escape.verification_gap"

FUNCTIONAL_RESPONSE = {
    "fields": {
        "customer_experience": {
            "value": "异常掉电恢复后关键业务参数不可用，影响客户继续生产",
            "evidence_ids": [EVIDENCE_ID],
            "confidence": 0.90,
        },
        "expected_quality_state": {
            "value": "异常掉电并重新上电后关键业务参数应保持一致并可正确恢复",
            "evidence_ids": [EVIDENCE_ID],
            "confidence": 0.90,
        },
        "lifecycle_stage": {
            "value": "运行执行",
            "evidence_ids": [EVIDENCE_ID],
            "confidence": 0.90,
        },
        "business_activity_scene": {
            "value": "掉电数据保持与上电恢复",
            "evidence_ids": [EVIDENCE_ID],
            "confidence": 0.90,
        },
        "failure_mode": {
            "value": "掉电恢复后关键参数未按预期恢复",
            "evidence_ids": [EVIDENCE_ID],
            "confidence": 0.85,
        },
        "quality_risk": {
            "value": "关键业务参数的数据完整性与恢复正确性",
            "evidence_ids": [EVIDENCE_ID],
            "confidence": 0.85,
        },
        "trigger_condition": {
            "value": "设备运行中发生异常掉电并重新上电",
            "evidence_ids": [EVIDENCE_ID],
            "confidence": 0.85,
        },
        "verification_method": {
            "value": "执行异常掉电、重复上电恢复和长稳组合验证",
            "evidence_ids": [EVIDENCE_ID],
            "confidence": 0.80,
        },
        "test_requirement": {
            "value": "覆盖异常边界、连续掉电恢复与状态残留组合场景",
            "evidence_ids": [EVIDENCE_ID],
            "confidence": 0.80,
        },
    },
    "lifecycle_code": "RUNTIME_EXECUTION",
    "activity_code": "POWER_LOSS_RETENTION_RECOVERY",
    "match_reason": "W4 functional Golden controlled provider response",
    "missing_condition": "",
    "questions": [],
}


def wait_ready(host: str, port: int, timeout_seconds: float = 10.0) -> None:
    deadline = time.time() + timeout_seconds
    url = f"http://{host}:{port}/__mock__/counters"
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            time.sleep(0.1)
    raise SystemExit("W4_FUNCTIONAL_PROVIDER_NOT_READY")


def configure(host: str, port: int) -> None:
    wait_ready(host, port)
    body = json.dumps(
        {
            "scenario_key": "default",
            "payload": FUNCTIONAL_RESPONSE,
            "behavior": {},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        f"http://{host}:{port}/__mock__/scenario",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=2) as response:
        if response.status != 200:
            raise SystemExit(f"W4_FUNCTIONAL_PROVIDER_CONFIG_FAILED:{response.status}")
    print("W4_FUNCTIONAL_PROVIDER=CONTROLLED_OPENAI_MOCK")
    print("W4_FUNCTIONAL_PROVIDER_CONTRACT=UNIFIED_RUNTIME_OPENAI_COMPATIBLE")
    print("REAL_PROVIDER_GOLDEN=NO")
    print("W4_FUNCTIONAL_PROVIDER_READY=YES")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18090)
    args = parser.parse_args()
    configure(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
