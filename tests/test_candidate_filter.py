from analysis.candidate_filter import CandidateFilter


def _context(query_spdt: str = "SPDT-A", case_spdt: str = "SPDT-A") -> dict:
    return {
        "query": {"standard_query": {"organization": {"SPDT": query_spdt}}},
        "case": {"enriched_case": {"SPDT": case_spdt}},
    }


def test_strict_department_filter_rejects_mismatch() -> None:
    decision = CandidateFilter({"department": {"mode": "strict"}}).evaluate(_context("A", "B"))
    assert decision.accepted is False


def test_strict_department_filter_keeps_match() -> None:
    decision = CandidateFilter({"department": {"mode": "strict"}}).evaluate(_context())
    assert decision.accepted is True


def test_missing_department_is_not_hard_rejected() -> None:
    decision = CandidateFilter({"department": {"mode": "strict"}}).evaluate({"query": {}, "case": {}})
    assert decision.accepted is True


def test_preferred_keeps_mismatch() -> None:
    decision = CandidateFilter({"department": {"mode": "preferred"}}).evaluate(_context("A", "B"))
    assert decision.accepted is True
