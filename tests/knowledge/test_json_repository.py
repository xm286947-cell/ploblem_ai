from pathlib import Path

import pytest

from repositories import JsonArtifactRepository, RepositoryError


def test_save_load_and_list(tmp_path: Path) -> None:
    repository = JsonArtifactRepository(tmp_path)
    target = repository.save("knowledge/cases/C1.json", {"case_id": "C1"})
    assert repository.load(target, required=True) == {"case_id": "C1"}
    assert repository.list("knowledge/cases") == [target]


def test_invalid_json_optional_and_required(tmp_path: Path) -> None:
    repository = JsonArtifactRepository(tmp_path)
    path = tmp_path / "broken.json"
    path.write_text("{broken", encoding="utf-8")
    assert repository.load(path) is None
    with pytest.raises(RepositoryError, match="JSON_ARTIFACT_INVALID"):
        repository.load(path, required=True)


def test_rejects_path_escape(tmp_path: Path) -> None:
    repository = JsonArtifactRepository(tmp_path)
    with pytest.raises(RepositoryError, match="PATH_OUTSIDE_REPOSITORY_ROOT"):
        repository.load(tmp_path.parent / "outside.json")
