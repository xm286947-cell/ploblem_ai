from pathlib import Path

from analysis.candidate_filter import CandidateFilter
from builder.json_response import parse_json_object


def test_json_parser_repairs_missing_comma():
    data, repaired = parse_json_object('{\n  "a": "x"\n  "b": 2\n}')
    assert repaired is True
    assert data == {"a": "x", "b": 2}


def test_department_preferred_never_rejects():
    engine = CandidateFilter({"department": {"enabled": True, "mode": "preferred"}})
    decision = engine.evaluate({
        "query": {"standard_query": {"business_context": {"spdt": "A"}}},
        "case": {"standard_case": {"business_context": {"spdt": "B"}}},
    })
    assert decision.accepted is True
