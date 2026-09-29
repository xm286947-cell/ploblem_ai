from pathlib import Path

from quality_knowledge.current_problem_contract import (
    COMMON_PROBLEM_VIEW_IS_WORKBENCH,
    CONTRACT_VERSION,
    RELATION_CONTRACT_VERSION,
    CanonicalProblemIdentityV1,
)
from quality_knowledge.web.current_problem_associations import (
    build_current_problem_associations,
)


ROOT = Path(__file__).resolve().parents[1]


class _Service:
    def __init__(self, issue):
        self.issue = issue

    def get_issue(self, knowledge_id):
        if self.issue and self.issue.get("knowledge_id") == knowledge_id:
            return self.issue
        return None


class _Materials:
    def __init__(self, rows):
        self.rows = rows

    def materials_for_issue(self, knowledge_id):
        return list(self.rows)


def _issue(*, business_issue_id="ITR-R2-CONTRACT-1"):
    return {
        "knowledge_id": "K-R2-CONTRACT-1",
        "business_type": "PLC",
        "business_issue_id": business_issue_id,
        "issue_version_id": "QV-R2-CONTRACT-3",
        "version_no": 3,
        "normalized_json": '{"escape":{"is_escape":true}}',
    }


def test_canonical_identity_reuses_existing_problem_master_and_is_versioned():
    identity = CanonicalProblemIdentityV1.from_issue(_issue())
    assert identity is not None
    payload = identity.to_dict()

    assert payload["contract_version"] == "canonical-problem/v1"
    assert payload["canonical_problem_id"] == "PLC:ITR-R2-CONTRACT-1"
    assert payload["owner_domain"] == "EXISTING_PROBLEM"
    assert payload["master_object_ref"] == {
        "domain": "EXISTING_PROBLEM",
        "object_type": "quality_issue",
        "object_id": "K-R2-CONTRACT-1",
    }
    assert payload["source_problem_ref"]["contract_version"] == "source-problem-itr-ref/v1"
    assert payload["version_ref"] == {
        "issue_version_id": "QV-R2-CONTRACT-3",
        "version_no": 3,
    }
    assert payload["workbench_count"] == 4
    assert COMMON_PROBLEM_VIEW_IS_WORKBENCH is False


def test_non_itr_business_id_fails_closed_instead_of_becoming_canonical():
    assert CanonicalProblemIdentityV1.from_issue(_issue(business_issue_id="BUG-R2-1")) is None

    result = build_current_problem_associations(
        _Service(_issue(business_issue_id="BUG-R2-1")),
        _Materials(
            [
                {
                    "material_type": "ITR_CS",
                    "business_key": "BUG-R2-1",
                }
            ]
        ),
        "K-R2-CONTRACT-1",
    )
    assert result["contract_version"] == CONTRACT_VERSION
    assert result["identity"] is None
    assert result["canonical_problem_id"] == ""
    assert result["related_count"] == 0
    assert all(item["relation_status"] == "NO_RELATION" for item in result["relations"])
    assert all(item["href"] == "" for item in result["relations"])


def test_four_workbench_relation_contract_is_read_only_and_exact():
    result = build_current_problem_associations(
        _Service(_issue()),
        _Materials(
            [
                {
                    "material_type": "ITR_CS",
                    "business_key": "ITR-R2-CONTRACT-1CS",
                },
                {
                    "material_type": "SOFTWARE_OPERATION",
                    "business_key": "ITR-R2-CONTRACT-1CS",
                },
            ]
        ),
        "K-R2-CONTRACT-1",
    )

    assert result["canonical_problem_id"] == "PLC:ITR-R2-CONTRACT-1"
    assert result["related_count"] == 4
    assert [item["key"] for item in result["relations"]] == [
        "ITR",
        "RESOLUTION",
        "SOFTWARE_ASSESSMENT",
        "MISSED_TEST",
    ]
    assert all(
        item["relation_contract_version"] == RELATION_CONTRACT_VERSION
        for item in result["relations"]
    )
    assert all(item["access_mode"] == "READ_ONLY_PROJECTION" for item in result["relations"])
    assert all(item["permission_authority"] == "SOURCE_DOMAIN" for item in result["relations"])
    assert result["relations"][1]["href"] == "/p0/itr-resolution?q=ITR-R2-CONTRACT-1"
    assert result["relations"][2]["href"] == "/p0/software-assessment?q=ITR-R2-CONTRACT-1"
    assert result["relations"][3]["href"] == "/p0/missed-test-analysis?q=ITR-R2-CONTRACT-1"


def test_common_problem_view_is_explicitly_not_a_fifth_workbench():
    template = (ROOT / "quality_knowledge/web/templates/p0_issues.html").read_text(
        encoding="utf-8"
    )
    assert "data-common-problem-view" in template
    assert 'data-contract-version="canonical-problem/v1"' in template
    assert "不是第五个业务工作台" in template
    assert "四个业务工作台保留各自对象、状态、动作与权限" in template
