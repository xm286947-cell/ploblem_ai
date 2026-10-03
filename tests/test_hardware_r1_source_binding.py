from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quality_knowledge.web.p0_app import create_p0_app
from services.hardware_case_r1_preview_store import HardwareR1PreviewStore
from services.hardware_case_r1_runtime import (
    _R1StageCache,
    invalidate_hardware_r1_stage_cache,
)
from services.hardware_case_source_store import (
    HardwareCaseSourceError,
    HardwareCaseSourceStore,
)


MAINTAINER = {"X-Hardware-Case-Role": "MAINTAINER"}


def test_single_active_source_duplicate_delete_reupload(tmp_path: Path) -> None:
    store = HardwareCaseSourceStore(tmp_path / "hardware.db", tmp_path / "sources")
    first = store.register_active_bytes(
        "A0152",
        "A0152-first.docx",
        b"first-source",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert first["binding_status"] == "ACTIVE"
    assert first["source_id"] == hashlib.sha256(b"first-source").hexdigest()

    with pytest.raises(HardwareCaseSourceError) as exc:
        store.register_active_bytes("A0152", "A0152-duplicate.docx", b"second-source")
    assert exc.value.code == "SOURCE_ALREADY_EXISTS"

    deleted = store.delete_active_source("A0152", deleted_by="test")
    assert deleted["deleted"] is True
    assert store.list_delete_audit(business_case_id="A0152")[0]["source_id"] == first["source_id"]

    second = store.register_active_bytes(
        "A0152",
        "A0152-second.docx",
        b"second-source",
    )
    assert second["source_id"] != first["source_id"]
    assert store.get_active_source("A0152")["source_id"] == second["source_id"]


def test_formal_knowledge_reference_blocks_source_delete(tmp_path: Path) -> None:
    store = HardwareCaseSourceStore(tmp_path / "hardware.db", tmp_path / "sources")
    source = store.register_active_bytes("A0207", "A0207.docx", b"source")
    lock = store.add_formal_knowledge_reference(
        "A0207",
        "KO-HW-A0207",
        source_id=source["source_id"],
    )
    assert lock["reference_status"] == "ACTIVE"

    with pytest.raises(HardwareCaseSourceError) as exc:
        store.delete_active_source("A0207")
    assert exc.value.code == "SOURCE_IN_USE_BY_FORMAL_KNOWLEDGE"
    assert store.get_active_source("A0207")["source_id"] == source["source_id"]


def test_source_delete_invalidators_are_source_scoped_and_runtime_audit_preserved(
    tmp_path: Path,
) -> None:
    preview = HardwareR1PreviewStore(tmp_path / "preview.db")
    source_a = "a" * 64
    source_b = "b" * 64

    def snapshot(source_id: str) -> dict:
        return {
            "source": {"source_id": source_id, "file_name": "case.docx"},
            "identity": {
                "source_id": source_id,
                "business_case_id": "A1",
                "raw_title": "demo",
            },
        }

    preview.save(snapshot(source_a), {"runtime": {"run_id": "run-a"}})
    preview.save(snapshot(source_b), {"runtime": {"run_id": "run-b"}})
    assert preview.delete_source(source_a) == 1
    assert preview.latest(source_id=source_a) is None
    assert preview.latest(source_id=source_b) is not None

    runtime_db = tmp_path / "runtime.db"
    cache = _R1StageCache(runtime_db)
    cache.put(
        "STAGE_A",
        "key-a",
        {"value": "a"},
        {"run_id": "run-a"},
        source_id=source_a,
        validator_version="v",
        pipeline_version="p",
        schema_version="s",
        agent_config_hash="c",
        prompt_version="q",
    )
    cache.put(
        "STAGE_A",
        "key-b",
        {"value": "b"},
        {"run_id": "run-b"},
        source_id=source_b,
        validator_version="v",
        pipeline_version="p",
        schema_version="s",
        agent_config_hash="c",
        prompt_version="q",
    )

    # Runtime audit sentinel is in the same DB and must not be touched.
    with sqlite3.connect(runtime_db) as connection:
        connection.execute("CREATE TABLE runtime_audit_sentinel(run_id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO runtime_audit_sentinel(run_id) VALUES ('keep')")
        connection.commit()

    deleted = invalidate_hardware_r1_stage_cache(
        source_a,
        root=tmp_path,
        environ={"HARDWARE_CASE_RUNTIME_DB": str(runtime_db)},
    )
    assert deleted == 1
    assert cache.count() == 1
    with sqlite3.connect(runtime_db) as connection:
        assert connection.execute(
            "SELECT run_id FROM runtime_audit_sentinel"
        ).fetchone() == ("keep",)


def test_r1_word_snapshot_binds_source_and_delete_reupload_api(tmp_path: Path) -> None:
    # Minimal valid DOCX generated with the existing test helper shape.
    from zipfile import ZipFile

    docx = tmp_path / "A0152-demo.docx"
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body><w:p><w:r><w:t>MCU UART demo</w:t></w:r></w:p></w:body></w:document>"""
    with ZipFile(docx, "w") as archive:
        archive.writestr("word/document.xml", document)

    runtime_db = tmp_path / "runtime.db"
    client = TestClient(
        create_p0_app(
            tmp_path / "quality.db",
            project_root=tmp_path,
            hardware_case_db_path=tmp_path / "hardware.db",
            hardware_tree_upload_dir=tmp_path / "trees",
            hardware_case_source_root=tmp_path / "sources",
            enabled_domains={"HARDWARE_CASE"},
        )
    )
    # Point cache invalidation at the isolated runtime DB.
    client.app.state.hardware_case_source_store
    payload = docx.read_bytes()
    first = client.post(
        "/api/v2/hardware-cases/r1/word-snapshot",
        files={
            "file": (
                docx.name,
                payload,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        headers=MAINTAINER,
    )
    assert first.status_code == 200
    assert first.json()["source_binding"]["business_case_id"] == "A0152"

    duplicate = client.post(
        "/api/v2/hardware-cases/r1/word-snapshot",
        files={"file": (docx.name, payload)},
        headers=MAINTAINER,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "SOURCE_ALREADY_EXISTS"

    deleted = client.delete(
        "/api/v2/hardware-cases/r1/sources/A0152",
        headers=MAINTAINER,
    )
    assert deleted.status_code == 200
    assert deleted.json()["runtime_audit_preserved"] is True

    again = client.post(
        "/api/v2/hardware-cases/r1/word-snapshot",
        files={"file": (docx.name, payload)},
        headers=MAINTAINER,
    )
    assert again.status_code == 200
