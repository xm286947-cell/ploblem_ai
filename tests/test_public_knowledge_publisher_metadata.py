from pathlib import Path

from public_knowledge_rag.contracts import Chunk
from public_knowledge_rag.store import Store


def test_public_source_publisher_is_persisted_and_exposed(tmp_path: Path):
    store = Store(tmp_path / "pk")
    source_id, revision_id, created = store.import_source(
        "KIOXIA SLC NAND",
        None,
        "SLC NAND is 1-bit per cell.",
        "text/plain",
        "plain",
        "1",
        [Chunk(ordinal=0, text="SLC NAND is 1-bit per cell.", locator='{"line":1}')],
        publisher="KIOXIA Corporation",
        raw_bytes=b"SLC NAND is 1-bit per cell.",
        original_filename="kioxia.txt",
    )

    assert created is True
    listed = store.list_sources()
    assert listed[0]["source_id"] == source_id
    assert listed[0]["publisher"] == "KIOXIA Corporation"

    detail = store.get_source(source_id)
    assert detail is not None
    assert detail["source"]["publisher"] == "KIOXIA Corporation"
    assert detail["revisions"][0]["revision_id"] == revision_id


def test_duplicate_import_can_fill_legacy_missing_publisher(tmp_path: Path):
    store = Store(tmp_path / "pk")
    args = (
        "KIOXIA SLC NAND",
        None,
        "SLC NAND is 1-bit per cell.",
        "text/plain",
        "plain",
        "1",
        [Chunk(ordinal=0, text="SLC NAND is 1-bit per cell.", locator='{"line":1}')],
    )
    source_id, revision_id, created = store.import_source(
        *args,
        raw_bytes=b"same-public-source",
    )
    assert created is True
    assert store.get_source(source_id)["source"]["publisher"] is None

    same_source, same_revision, created_again = store.import_source(
        *args,
        publisher="KIOXIA Corporation",
        raw_bytes=b"same-public-source",
    )

    assert same_source == source_id
    assert same_revision == revision_id
    assert created_again is False
    assert (
        store.get_source(source_id)["source"]["publisher"]
        == "KIOXIA Corporation"
    )


def test_existing_publisher_is_not_silently_overwritten(tmp_path: Path):
    store = Store(tmp_path / "pk")
    args = (
        "KIOXIA SLC NAND",
        None,
        "SLC NAND is 1-bit per cell.",
        "text/plain",
        "plain",
        "1",
        [Chunk(ordinal=0, text="SLC NAND is 1-bit per cell.", locator='{"line":1}')],
    )
    source_id, _, _ = store.import_source(
        *args,
        publisher="KIOXIA Corporation",
        raw_bytes=b"same-public-source",
    )
    store.import_source(
        *args,
        publisher="Wrong Publisher",
        raw_bytes=b"same-public-source",
    )

    assert (
        store.get_source(source_id)["source"]["publisher"]
        == "KIOXIA Corporation"
    )
