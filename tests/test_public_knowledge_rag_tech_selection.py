from __future__ import annotations

from public_knowledge_rag.contracts import SearchHit
from public_knowledge_rag.tech_selection import (
    ParsedElement,
    SQLiteFTS5Index,
    StructuredDocument,
    StructureAwareChunker,
    HybridRRFIndex,
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


def test_chunker_keeps_table_atomic_and_page_resolvable() -> None:
    table = "| register | value |\n|---|---|\n" + "| field | 1 |\n" * 1200
    parsed = StructuredDocument(
        text=table,
        locators=("page:14",),
        parser_id="fixture",
        parser_version="1",
        elements=(ParsedElement("el-1", "table", table, 14, ("Status Register",), True),),
        page_count=14,
    )

    chunks = StructureAwareChunker().chunk(parsed)

    assert len(chunks) == 1
    assert chunks[0].text.endswith(table)
    assert '"page_start":14' in chunks[0].locator
    assert '"element_ids":["el-1"]' in chunks[0].locator
