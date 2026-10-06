from __future__ import annotations

from public_knowledge_rag.contracts import SearchHit
from types import SimpleNamespace
import json

import pytest

from public_knowledge_rag.tech_selection import (
    HybridRRFIndex,
    OllamaEmbeddingProvider,
    SQLiteFTS5Index,
    StructuredDocument,
    StructureAwareChunker,
)


class RecordingIndex:
    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = hits
        self.requested_limits: list[int] = []

    def search(self, query: str, top_k: int, source_ids: list[str] | None = None) -> list[SearchHit]:
        self.requested_limits.append(top_k)
        return self.hits[:top_k]


def _hit(index: int, source_id: str) -> SearchHit:
    return SearchHit(f"chunk-{index}", source_id, "rev-1", "{}", "evidence", 1.0)


def test_hybrid_retriever_keeps_baseline_candidate_depths_by_default() -> None:
    dense = RecordingIndex([_hit(i, f"dense-{i}") for i in range(40)])
    lexical = RecordingIndex([_hit(i + 100, f"lexical-{i}") for i in range(40)])

    hits = HybridRRFIndex(dense, lexical).search("test", 10)

    assert dense.requested_limits == [20]
    assert lexical.requested_limits == [20]
    assert len(hits) == 10


def test_hybrid_retriever_supports_one_global_candidate_depth_iteration() -> None:
    dense = RecordingIndex([_hit(i, f"dense-{i}") for i in range(40)])
    lexical = RecordingIndex([_hit(i + 100, f"lexical-{i}") for i in range(40)])

    hits = HybridRRFIndex(dense, lexical, dense_candidates=40, lexical_candidates=40).search("test", 10)

    assert dense.requested_limits == [40]
    assert lexical.requested_limits == [40]
    assert len(hits) == 10


def test_fts5_indexes_content_and_respects_source_scope(tmp_path) -> None:
    index = SQLiteFTS5Index(tmp_path / "chunks.sqlite3")
    index.upsert([
        {"chunk_id": "a", "source_id": "M01", "source_revision": "r1", "locator": "{}", "title": "NAND", "text": "P_FAIL program failure status"},
        {"chunk_id": "b", "source_id": "M02", "source_revision": "r2", "locator": "{}", "title": "NOR", "text": "WIP operation in progress"},
    ])

    all_hits = index.search("P_FAIL", 10)
    scoped_hits = index.search("P_FAIL WIP", 10, source_ids=["M02"])

    assert [hit.hit_id for hit in all_hits] == ["a"]
    assert [hit.hit_id for hit in scoped_hits] == ["b"]


def test_chunker_requires_native_docling_document() -> None:
    parsed = StructuredDocument(
        text="table",
        locators=("page:14",),
        parser_id="fixture",
        parser_version="1",
        elements=(),
        page_count=14,
    )
    with pytest.raises(ValueError, match="native parsed DoclingDocument"):
        StructureAwareChunker().chunk(parsed)


def test_chunker_keeps_docling_headings_captions_and_page_locator() -> None:
    item = SimpleNamespace(
        prov=[SimpleNamespace(page_no=14)],
        label=SimpleNamespace(value="table"),
        self_ref="#/tables/2",
    )
    native_doc = object()
    parsed = StructuredDocument(
        text="status table",
        locators=("page:14",),
        parser_id="fixture",
        parser_version="1",
        page_count=14,
        native_document=native_doc,
    )
    chunker = StructureAwareChunker()

    class FakeHybrid:
        tokenizer = SimpleNamespace(count_tokens=lambda text: len(text.split()))
        merge_peers = True
        repeat_table_header = True

        def chunk(self, doc):
            assert doc is native_doc
            return [SimpleNamespace(text="table content", meta=SimpleNamespace(doc_items=[item], headings=["Status Register"], captions=["Table 4"]))]

        def contextualize(self, doc_chunk):
            return "Status Register\nTable 4\n" + doc_chunk.text

    chunker.hybrid = FakeHybrid()
    chunks = chunker.chunk(parsed)

    assert len(chunks) == 1
    assert chunks[0].text.startswith("Status Register\nTable 4")
    locator = json.loads(chunks[0].locator)
    assert locator["page_start"] == 14
    assert locator["headings"] == ["Status Register"]
    assert locator["captions"] == ["Table 4"]
    assert locator["element_refs"] == ["#/tables/2"]
    assert locator["table_count"] == 1


def test_metadata_scope_only_filters_explicit_query_entities_and_class() -> None:
    catalog = [
        {"source_id": "gd25", "title": "GigaDevice GD25Q64E", "source_revision": "Rev 1.6", "source_class": "datasheet"},
        {"source_id": "winbond", "title": "Winbond W25Q128JV", "source_revision": "Rev 1.3", "source_class": "datasheet"},
        {"source_id": "nvme", "title": "NVM Express Base Specification 2.0D", "source_revision": "2.0D", "source_class": "standard"},
    ]
    ids, evidence = HybridRRFIndex.explicit_metadata_scope("GD25Q64E 规格书 Rev1.6", catalog)
    assert ids == ["gd25"]
    assert {x["kind"] for x in evidence["reasons"]} == {"explicit_entity", "explicit_source_class"}

    ids, evidence = HybridRRFIndex.explicit_metadata_scope("NVMe 2.0D 标准", catalog)
    assert ids == ["nvme"]
    assert evidence["applied"] is True

    ids, evidence = HybridRRFIndex.explicit_metadata_scope("该器件的寿命参数是什么？", catalog)
    assert ids is None
    assert evidence == {"applied": False, "reasons": []}

    ids, evidence = HybridRRFIndex.explicit_metadata_scope("P/E endurance condition", catalog)
    assert ids is None
    assert evidence == {"applied": False, "reasons": []}


def test_embedding_provider_applies_frozen_qwen_query_instruction(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"embeddings":[[0.1,0.2]]}'

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("public_knowledge_rag.tech_selection.urllib.request.urlopen", fake_urlopen)
    provider = OllamaEmbeddingProvider(base_url="http://ollama", timeout=17)
    assert provider.embed(["What is P/E endurance?"], query=True) == [[0.1, 0.2]]
    assert captured["payload"]["input"][0] == provider.QUERY_INSTRUCTION + "What is P/E endurance?"
    assert captured["payload"]["truncate"] is False
    assert captured["timeout"] == 17
