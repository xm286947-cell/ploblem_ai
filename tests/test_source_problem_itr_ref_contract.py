from __future__ import annotations

from pathlib import Path

import pytest

from quality_knowledge.major_cases.context import RepositoryMajorProblemContextProvider
from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
from quality_knowledge.problem_refs import (
    CONTRACT_VERSION,
    InvalidSourceProblemItrRef,
    SourceProblemItrNotFound,
    SourceProblemItrRefResolver,
    SourceProblemItrRefV1,
    normalize_itr,
)


def test_r01_canonical_normalize() -> None:
    assert normalize_itr("itr001") == "ITR001"
    assert normalize_itr("ITR 001") == "ITR001"
    assert normalize_itr("ITR001") == "ITR001"


def test_r02_itr_cs_compatibility_does_not_create_second_identity() -> None:
    plain = SourceProblemItrRefV1.from_input("ITR001")
    cs = SourceProblemItrRefV1.from_input("ITR001CS")
    assert cs.public_ref == plain.public_ref == "ITR001"
    assert cs.to_dict()["source_refs"] == ["ITR001"]


def test_r03_public_ref_is_stable_across_source_changes() -> None:
    values = [
        {"title": "old", "status": "OPEN", "version": "1"},
        {"title": "new", "status": "RESOLVED", "version": "2"},
    ]
    refs = [SourceProblemItrRefV1.from_input(" itr 001 ").public_ref for _ in values]
    assert refs == ["ITR001", "ITR001"]


def test_r04_internal_ids_are_not_public_identity() -> None:
    payload = SourceProblemItrRefV1.from_input("ITR001").to_dict()
    assert set(payload) == {
        "contract_version",
        "ref_type",
        "public_ref",
        "canonical_itr",
        "source_status",
        "source_refs",
    }
    assert not set(payload) & {
        "event_id",
        "source_link_id",
        "material_id",
        "record_id",
        "case_id",
    }
    assert payload["public_ref"] != "KEVT-internal"


def test_r05_invalid_ref_does_not_fallback_to_an_internal_id() -> None:
    for value in (None, "", "   ", "CASE-1", "event-1"):
        with pytest.raises(InvalidSourceProblemItrRef) as error:
            SourceProblemItrRefV1.from_input(value)
        assert error.value.code == "INVALID_REF"


def test_r06_valid_but_not_found_is_distinct_from_invalid() -> None:
    resolver = SourceProblemItrRefResolver(lambda _public_ref: None)
    with pytest.raises(SourceProblemItrNotFound) as not_found:
        resolver.resolve("ITR404")
    assert not_found.value.code == "REF_VALID_BUT_SOURCE_NOT_FOUND"
    with pytest.raises(InvalidSourceProblemItrRef) as invalid:
        resolver.resolve("not-an-itr")
    assert invalid.value.code == "INVALID_REF"


def test_r07_major_context_uses_the_same_public_ref(tmp_path: Path) -> None:
    repository = MajorKnowledgeRepository(tmp_path / "major.db", tmp_path / "attachments")
    case = repository.create_case("Problem", "GROUP-1")
    event = repository.upsert_event(
        case["case_id"], standard_itr="itr 001cs", internal_event_key="E-1"
    )
    repository.add_source_link(
        case["case_id"],
        event["event_id"],
        {"record_id": "SOURCE-1", "product": {"product_code": "P-1"}},
        standard_itr="ITR001",
        role="CURRENT_EVENT",
        status="LINKED",
    )

    context = RepositoryMajorProblemContextProvider(repository).get_context("ITR001CS")
    assert context is not None
    assert context["problem_id"] == "ITR001"
    assert context["source_refs"] == ["ITR001"]
    assert SourceProblemItrRefV1.from_input("ITR001").public_ref == context["problem_id"]
    assert context["contract_version"] == "major-problem-context/v1"


def test_r08_public_boundary_has_no_cross_domain_or_repository_exposure() -> None:
    source = Path(__import__("quality_knowledge.problem_refs", fromlist=["__file__"]).__file__).read_text(
        encoding="utf-8"
    )
    assert source.count("def normalize_itr") == 1
    assert "MajorKnowledgeRepository" not in source
    assert "quality_knowledge.p04" not in source
    assert "quality_scenario" not in source.lower()
    assert "SELECT " not in source
    assert CONTRACT_VERSION == "source-problem-itr-ref/v1"
