from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quality_knowledge.web.p0_app import create_p0_app
from services.hardware_asset_repository import CandidateAssetRepository
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


def _source_store(tmp_path: Path) -> HardwareCaseSourceStore:
    hardware_db = tmp_path / "hardware.db"
    CandidateAssetRepository(tmp_path / "hardware_asset.db").initialize()
    return HardwareCaseSourceStore(hardware_db, tmp_path / "sources")


def test_single_active_source_duplicate_delete_reupload(tmp_path: Path) -> None:
    store = _source_store(tmp_path)
    first = store.register_active_bytes(
        "A0152",
        "A0152-first.docx",
        b"first-source",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert first["binding_status"] == "ACTIVE"
    assert first["source_id"] == hashlib.sha256(b"first-source").hexdigest()

    reused = store.register_active_bytes(
        "A0152", "A0152-repeat.docx", b"first-source"
    )
    assert reused["source_id"] == first["source_id"]
    assert reused["idempotent_reuse"] is True

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
    store = _source_store(tmp_path)
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

    CandidateAssetRepository(tmp_path / "hardware_asset.db").initialize()
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
    assert duplicate.status_code == 200
    assert duplicate.json()["source_binding"]["source_id"] == first.json()["source_binding"]["source_id"]
    assert duplicate.json()["source_binding"]["idempotent_reuse"] is True

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


def test_batch_workbench_source_persistence_gate(tmp_path: Path) -> None:
    from zipfile import ZipFile

    def docx_bytes(name: str, text: str) -> bytes:
        path = tmp_path / name
        document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"""
        with ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", document)
        return path.read_bytes()

    CandidateAssetRepository(tmp_path / "hardware_asset.db").initialize()
    client = TestClient(
        create_p0_app(
            tmp_path / "quality-batch.db",
            project_root=tmp_path,
            hardware_case_db_path=tmp_path / "hardware-batch.db",
            hardware_tree_upload_dir=tmp_path / "trees-batch",
            hardware_case_source_root=tmp_path / "sources-batch",
            enabled_domains={"HARDWARE_CASE"},
        )
    )

    a0152 = docx_bytes("A0152-batch-demo.docx", "MCU UART demo")
    a0156 = docx_bytes("A0156-batch-demo.docx", "test point resistor demo")

    created = client.post(
        "/api/v2/hardware-cases/r1/workbench/batches",
        files=[
            (
                "files",
                (
                    "A0152-batch-demo.docx",
                    a0152,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ),
            ),
            (
                "files",
                (
                    "A0156-batch-demo.docx",
                    a0156,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ),
            ),
            ("files", ("bad.pdf", b"not-a-docx", "application/pdf")),
        ],
        headers=MAINTAINER,
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["summary"]["TOTAL"] == 3

    items = {item["source_file"]: item for item in payload["items"]}
    assert items["A0152-batch-demo.docx"]["result"] == "QUEUED"
    assert items["A0156-batch-demo.docx"]["result"] == "QUEUED"
    assert items["bad.pdf"]["result"] == "FAILED"
    assert items["bad.pdf"]["failed_stage"] == "PARSE"

    source_a = client.get(
        "/api/v2/hardware-cases/r1/sources/A0152",
        headers=MAINTAINER,
    )
    source_b = client.get(
        "/api/v2/hardware-cases/r1/sources/A0156",
        headers=MAINTAINER,
    )
    assert source_a.status_code == 200
    assert source_b.status_code == 200
    assert source_a.json()["source_id"] == hashlib.sha256(a0152).hexdigest()
    assert source_b.json()["source_id"] == hashlib.sha256(a0156).hexdigest()

    duplicate = client.post(
        "/api/v2/hardware-cases/r1/workbench/batches",
        files=[
            (
                "files",
                (
                    "A0152-batch-demo.docx",
                    a0152,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ),
            )
        ],
        headers=MAINTAINER,
    )
    assert duplicate.status_code == 201
    duplicate_item = duplicate.json()["items"][0]
    assert duplicate_item["result"] == "QUEUED"
    assert duplicate_item["source_id"] == source_a.json()["source_id"]

    deleted = client.delete(
        "/api/v2/hardware-cases/r1/sources/A0152",
        headers=MAINTAINER,
    )
    assert deleted.status_code == 200
    assert deleted.json()["runtime_audit_preserved"] is True

    reuploaded = client.post(
        "/api/v2/hardware-cases/r1/workbench/batches",
        files=[
            (
                "files",
                (
                    "A0152-batch-demo.docx",
                    a0152,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ),
            )
        ],
        headers=MAINTAINER,
    )
    assert reuploaded.status_code == 201
    assert reuploaded.json()["items"][0]["result"] == "QUEUED"
