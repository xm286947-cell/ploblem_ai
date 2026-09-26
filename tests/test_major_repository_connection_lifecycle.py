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


def _write_document(directory: Path, name: str, content: bytes) -> Path:
    source = directory / name
    source.write_bytes(content)
    return source


def _assert_document_version_contract(result: dict, source: Path, action: str) -> None:
    assert result["action"] == action
    assert result["version_id"]
    assert result["document_id"]
    assert result["version_no"] >= 1
    assert result["content_hash"]
    assert result["attachment_path"]
    assert result["original_filename"] == source.name


def test_document_version_filename_contract_new(tmp_path: Path) -> None:
    repo = MajorKnowledgeRepository(tmp_path / "major.sqlite3", tmp_path / "attachments")
    case = repo.create_case("New source filename", "DEV-T01")
    source = _write_document(tmp_path, "DEV-T01 fresh source.docx", b"new document bytes")

    result = repo.ingest_file(case["case_id"], source)

    _assert_document_version_contract(result, source, "NEW")
    assert result["version_no"] == 1


def test_document_version_filename_contract_updated(tmp_path: Path) -> None:
    repo = MajorKnowledgeRepository(tmp_path / "major.sqlite3", tmp_path / "attachments")
    case = repo.create_case("Updated source filename", "DEV-T02")
    first_source = _write_document(tmp_path, "DEV-T02 original.docx", b"original document bytes")
    first = repo.ingest_file(case["case_id"], first_source)
    source = _write_document(tmp_path, "DEV-T02 updated source.docx", b"updated document bytes")

    result = repo.ingest_file(case["case_id"], source, document_id=first["document_id"])

    _assert_document_version_contract(result, source, "UPDATED")
    assert result["version_no"] == 2


def test_document_version_filename_contract_skipped(tmp_path: Path) -> None:
    repo = MajorKnowledgeRepository(tmp_path / "major.sqlite3", tmp_path / "attachments")
    case = repo.create_case("Skipped source filename", "DEV-T03")
    source = _write_document(tmp_path, "DEV-T03 duplicate source.docx", b"same document bytes")
    repo.ingest_file(case["case_id"], source)

    result = repo.ingest_file(case["case_id"], source)

    _assert_document_version_contract(result, source, "SKIPPED")
