from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from quality_knowledge.major_cases.repository import MajorKnowledgeRepository


def test_repository_context_manager_releases_sqlite_handle(tmp_path: Path) -> None:
    repo = MajorKnowledgeRepository(tmp_path / "major.sqlite3", tmp_path / "attachments")

    connection = repo.connect()
    with connection as active:
        active.execute("SELECT 1").fetchone()

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_repository_database_can_be_deleted_after_normal_operations(tmp_path: Path) -> None:
    db_path = tmp_path / "major.sqlite3"
    repo = MajorKnowledgeRepository(db_path, tmp_path / "attachments")

    case = repo.create_case("Windows handle release", "TEST", domain="PLC")
    repo.get_case(case["case_id"])
    repo.list_cases(group_code="TEST")

    db_path.unlink()
    assert not db_path.exists()
