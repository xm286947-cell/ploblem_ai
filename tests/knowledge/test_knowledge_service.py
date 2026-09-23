from pathlib import Path

from repositories import JsonArtifactRepository
from services import KnowledgeService


def test_load_case_artifacts_prefers_retrieval_source_path(tmp_path: Path) -> None:
    repository = JsonArtifactRepository(tmp_path)
    repository.save("knowledge/retrieval_docs/C1.json", {
        "case_id": "C1",
        "source_case_path": "knowledge/custom/C1.json",
    })
    repository.save("knowledge/custom/C1.json", {"source": "custom"})
    repository.save("knowledge/enriched_case/C1.json", {"source": "fallback"})
    repository.save("knowledge/standard_case/C1.json", {"case_id": "C1"})
    service = KnowledgeService(repository)

    artifacts = service.load_case_artifacts("C1")

    assert artifacts.enriched_case == {"source": "custom"}
    assert artifacts.standard_case == {"case_id": "C1"}
    assert artifacts.paths["enriched_case"] == tmp_path / "knowledge/custom/C1.json"


def test_load_query_inputs(tmp_path: Path) -> None:
    repository = JsonArtifactRepository(tmp_path)
    repository.save("knowledge/standard_query/Q1.json", {"query_id": "Q1"})
    repository.save("knowledge/retrieval_profile/Q1.json", {"query_id": "Q1"})
    service = KnowledgeService(repository)

    standard_query, retrieval_profile = service.load_query_inputs("Q1")

    assert standard_query["query_id"] == "Q1"
    assert retrieval_profile["query_id"] == "Q1"


def test_analysis_artifact_access_and_persistence(tmp_path: Path) -> None:
    repository = JsonArtifactRepository(tmp_path)
    repository.save("knowledge/analysis_context/Q1/C1.json", {"query_id": "Q1", "case_id": "C1"})
    repository.save("knowledge/similarity_analysis/Q1/C1.json", {"analysis_status": "SUCCESS"})
    service = KnowledgeService(repository)

    files = service.list_analysis_contexts(query_id="Q1")
    artifacts = service.load_analysis_artifacts("Q1", "C1", context_path=files[0])

    assert files == [tmp_path / "knowledge/analysis_context/Q1/C1.json"]
    assert artifacts.analysis_context["case_id"] == "C1"
    assert artifacts.similarity_analysis == {"analysis_status": "SUCCESS"}
    assert artifacts.solution_analysis is None

    similarity_path = service.save_similarity_analysis("Q1", "C1", {"score": 0.9})
    solution_path = service.save_solution_analysis("Q1", "C1", {"solution": "x"})
    repeat_path = service.save_repeat_analysis("Q1", {"decision": "REPEAT"})

    assert repository.load(similarity_path, required=True) == {"score": 0.9}
    assert repository.load(solution_path, required=True) == {"solution": "x"}
    assert repository.load(repeat_path, required=True) == {"decision": "REPEAT"}


def test_list_analysis_contexts_supports_case_filter(tmp_path: Path) -> None:
    repository = JsonArtifactRepository(tmp_path)
    repository.save("knowledge/analysis_context/Q1/C1.json", {"case_id": "C1"})
    repository.save("knowledge/analysis_context/Q1/C2.json", {"case_id": "C2"})
    service = KnowledgeService(repository)

    assert service.list_analysis_contexts(query_id="Q1", case_id="C2") == [
        tmp_path / "knowledge/analysis_context/Q1/C2.json"
    ]
    assert service.list_analysis_contexts(query_id="Q1", case_id="MISSING") == []
