from __future__ import annotations

import json
import tempfile
from pathlib import Path

from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from repositories import JsonArtifactRepository
from retriever.case_retriever import QueryInput
from services.historical_case_contract import HistoricalCaseConsumerService
from services.major_case_publisher import MajorCasePublisher


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="major-case-mvp-") as temp:
        root = Path(temp)
        major = MajorKnowledgeRepository(root / "major.sqlite3", root / "attachments")
        artifacts = JsonArtifactRepository(root / "published")

        case = major.create_case("控制器重启重大问题", "DEMO", domain="PLC")
        event = major.upsert_event(
            case["case_id"],
            standard_itr="ITR-MVP-DEMO-001",
            internal_event_key="ITR-MVP-DEMO-001",
            title="ITR-MVP-DEMO-001",
        )

        def add(entry_type: str, content: str) -> None:
            major.add_entry(
                case["case_id"],
                entry_type,
                content,
                assertion_kind="FACT",
                origin="HUMAN",
                status="CONFIRMED",
                event_id=event["event_id"],
                evidence=[{
                    "locator": entry_type.lower(),
                    "excerpt": f"证据：{content}",
                }],
            )

        add("ISSUE_FACT", "控制器周期性重启")
        add("ROOT_CAUSE", "CAN 接收队列缺少流控")
        add("ACTION", "增加队列水位保护")
        add("VERIFICATION", "长稳回归通过")
        major.update_case_status(case["case_id"], "ACTIVE")

        publish = MajorCasePublisher(major, artifacts).publish_event(event["event_id"])
        case_id = publish["case_id"]

        consumer = HistoricalCaseConsumerService(
            artifacts,
            repeat_search=lambda _query, _top_k: {
                "results": [{
                    "case_id": case_id,
                    "title": "控制器重启重大问题",
                    "summary": "控制器周期性重启",
                    "score": 1.0,
                    "rank": 1,
                    "reasons": ["MVP golden path smoke"],
                }]
            },
        )

        search = consumer.search_repeat_cases(QueryInput(text="控制器重启"))
        detail = consumer.get_case(search["candidates"][0]["case_id"])

        assert publish["publication_status"] == "PUBLISHED"
        assert publish["status"] == "CREATED"
        assert search["contract_version"] == "historical-case/v1"
        assert detail["case_id"] == case_id
        assert detail["problem_description"] == "控制器周期性重启"
        assert detail["root_cause"] == "CAN 接收队列缺少流控"
        assert detail["solution"] == "增加队列水位保护"
        assert detail["verification_result"] == "长稳回归通过"
        assert detail["evidence"]

        print("RESULT=PASS")
        print("GOLDEN_PATH=Major Confirmed -> Publish -> Search -> Detail -> Evidence")
        print(f"CASE_ID={case_id}")
        print(f"EVIDENCE_COUNT={len(detail['evidence'])}")
        print("DETAIL=" + json.dumps(detail, ensure_ascii=False, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
