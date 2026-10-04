"""Baseline adapters for the frozen Public Knowledge RAG technical spike.

These adapters implement the existing Parser, Chunker, EmbeddingProvider,
Index, and Retriever contracts. They remain opt-in and do not change the
infrastructure reference service's default lexical behavior.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import re
import tempfile
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from qdrant_client import QdrantClient, models
from docling_core.types.doc.items.table.table import TableItem
from docling.chunking import HybridChunker
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer

from .contracts import Chunk, Index, ParsedDocument, Retriever, SearchHit


@dataclass(frozen=True)
class ParsedElement:
    element_id: str
    label: str
    text: str
    page_no: int | None
    section_path: tuple[str, ...]
    is_table: bool = False


@dataclass(frozen=True)
class StructuredDocument(ParsedDocument):
    elements: tuple[ParsedElement, ...] = ()
    page_count: int = 0
    native_document: Any | None = None


class DoclingParser:
    parser_id = "docling"
    parser_version = "2.133.0"

    def __init__(self) -> None:
        pdf_options = PdfPipelineOptions(do_ocr=False, do_table_structure=True)
        self.converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options)}
        )

    def parse(self, content: bytes, media_type: str) -> StructuredDocument:
        media = media_type.split(";", 1)[0].strip().lower()
        suffix_by_media = {
            "application/pdf": ".pdf",
            "text/html": ".html",
            "application/xhtml+xml": ".html",
            "text/markdown": ".md",
            "text/plain": ".txt",
        }
        suffix = suffix_by_media.get(media)
        if suffix is None:
            raise ValueError(f"Unsupported frozen source media type: {media}")
        with tempfile.NamedTemporaryFile(suffix=suffix) as source_file:
            source_file.write(content)
            source_file.flush()
            result = self.converter.convert(source_file.name)
        if not result.document:
            raise ValueError(f"Docling conversion returned no document: {result.status}")

        elements: list[ParsedElement] = []
        heading_stack: list[tuple[int, str]] = []
        for ordinal, (item, _depth) in enumerate(result.document.iterate_items()):
            label_value = getattr(item, "label", "")
            label = getattr(label_value, "value", str(label_value))
            item_text = getattr(item, "text", "") or ""
            is_table = isinstance(item, TableItem)
            if is_table:
                text = item.export_to_markdown(result.document).strip()
            else:
                text = item_text.strip()
            if not text:
                continue
            is_heading = "section_header" in label.lower() or label.lower() == "title"
            if is_heading:
                text = f"{'#' * max(1, int(getattr(item, 'level', 1) or 1))} {item_text.strip()}"
            if is_heading:
                level = int(getattr(item, "level", 1) or 1)
                heading_stack = [entry for entry in heading_stack if entry[0] < level]
                heading_stack.append((level, item_text.strip() or text))
            prov = getattr(item, "prov", None) or []
            page_no = getattr(prov[0], "page_no", None) if prov else None
            elements.append(
                ParsedElement(
                    element_id=f"el-{ordinal:06d}",
                    label=label,
                    text=text,
                    page_no=int(page_no) if page_no is not None else None,
                    section_path=tuple(entry[1] for entry in heading_stack),
                    is_table=is_table,
                )
            )
        markdown = result.document.export_to_markdown()
        pages = len(getattr(result.document, "pages", {}) or {})
        return StructuredDocument(
            text=markdown,
            locators=tuple(f"page:{n}" for n in range(1, pages + 1)),
            parser_id=self.parser_id,
            parser_version=self.parser_version,
            elements=tuple(elements),
            page_count=pages,
            native_document=result.document,
        )


class StructureAwareChunker:
    """Docling-native token-aware chunking with structural locators."""

    def __init__(self, target_tokens: int = 800) -> None:
        if target_tokens < 100:
            raise ValueError("Invalid token budget")
        # HybridChunker preserves headings/captions, merges peer elements, and
        # repeats table headers when oversized tables need to be split.
        self.target = target_tokens
        default_tokenizer = HybridChunker().tokenizer
        tokenizer = HuggingFaceTokenizer(tokenizer=default_tokenizer.tokenizer, max_tokens=target_tokens)
        self.hybrid = HybridChunker(tokenizer=tokenizer, merge_peers=True, repeat_table_header=True)

    def token_count(self, text: str) -> int:
        return int(self.hybrid.tokenizer.count_tokens(text))

    def chunk(self, parsed: StructuredDocument) -> list[Chunk]:
        if parsed.native_document is None:
            raise ValueError("Docling HybridChunker requires the native parsed DoclingDocument")
        out: list[Chunk] = []
        for doc_chunk in self.hybrid.chunk(parsed.native_document):
            meta = doc_chunk.meta
            items = list(meta.doc_items or [])
            pages = [int(prov.page_no) for item in items for prov in (getattr(item, "prov", None) or [])
                     if getattr(prov, "page_no", None) is not None]
            labels = [getattr(getattr(item, "label", None), "value", str(getattr(item, "label", ""))) for item in items]
            refs = [str(getattr(item, "self_ref", "")) for item in items]
            headings = list(meta.headings or [])
            captions = list(meta.captions or [])
            locator = json.dumps(
                {
                    "page_start": min(pages) if pages else None,
                    "page_end": max(pages) if pages else None,
                    "headings": headings,
                    "captions": captions,
                    "element_refs": refs,
                    "labels": labels,
                    "table_count": sum(1 for label in labels if "table" in label.lower()),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            # Contextualize includes heading and caption context in retrieval text.
            out.append(Chunk(len(out), self.hybrid.contextualize(doc_chunk), locator))
        return out



class OllamaEmbeddingProvider:
    provider_id = "ollama-remote-qwen3-embedding-0.6b"
    QUERY_INSTRUCTION = (
        "Instruct: Given a query about storage-device specifications or storage-software behavior, "
        "retrieve relevant passages that answer the query.\nQuery: "
    )

    def __init__(self, base_url: str | None = None, model: str | None = None, timeout: float = 180.0) -> None:
        self.base_url = (base_url or os.getenv("OLLAMA_URL", "http://192.168.1.100:11434")).rstrip("/")
        self.model = model or os.getenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b")
        self.timeout = timeout

    def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        if not texts:
            return []
        inputs = [self.QUERY_INSTRUCTION + text for text in texts] if query else texts
        payload = json.dumps({"model": self.model, "input": inputs, "truncate": False}).encode()
        request = urllib.request.Request(
            f"{self.base_url}/api/embed", data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read())
        vectors = result.get("embeddings", [])
        if len(vectors) != len(texts):
            raise RuntimeError(f"Embedding batch cardinality mismatch: {len(vectors)} != {len(texts)}")
        dims = {len(vector) for vector in vectors}
        if len(dims) != 1:
            raise RuntimeError("Embedding batch dimensions are inconsistent")
        return vectors


class SQLiteFTS5Index(Index):
    adapter_id = "sqlite-fts5"

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with self.connect() as db:
            db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                chunk_id UNINDEXED, source_id UNINDEXED, source_revision UNINDEXED,
                locator UNINDEXED, title UNINDEXED, content, tokenize='unicode61'
            )""")

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=60)

    def upsert(self, records: list[dict[str, Any]]) -> None:
        with self.connect() as db:
            db.executemany("DELETE FROM chunks_fts WHERE chunk_id=?", [(r["chunk_id"],) for r in records])
            db.executemany(
                "INSERT INTO chunks_fts(chunk_id,source_id,source_revision,locator,title,content) VALUES(?,?,?,?,?,?)",
                [(r["chunk_id"], r["source_id"], r["source_revision"], r["locator"], r["title"], r["text"]) for r in records],
            )

    def search(self, query: str, top_k: int, source_ids: list[str] | None = None) -> list[SearchHit]:
        tokens = [t for t in query.replace('"', " ").split() if len(t) > 1]
        if not tokens:
            return []
        # OR improves recall for technical questions; phrase tokens avoid FTS syntax injection.
        expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
        where = "chunks_fts MATCH ?"
        params: list[Any] = [expression]
        if source_ids:
            where += " AND source_id IN (" + ",".join("?" for _ in source_ids) + ")"
            params.extend(source_ids)
        params.append(top_k)
        with self.connect() as db:
            rows = db.execute(
                f"SELECT chunk_id,source_id,source_revision,locator,content,bm25(chunks_fts) AS rank FROM chunks_fts WHERE {where} ORDER BY rank LIMIT ?",
                params,
            ).fetchall()
        return [SearchHit(r[0], r[1], r[2], r[3], r[4], 1.0 / (1.0 + abs(float(r[5])))) for r in rows]


class QdrantDenseIndex(Index):
    adapter_id = "qdrant-dense"

    def __init__(self, url: str, collection: str, embedding: OllamaEmbeddingProvider, dimension: int = 1024) -> None:
        self.client = QdrantClient(url=url, timeout=120)
        self.collection = collection
        self.embedding = embedding
        self.dimension = dimension
        if not self.client.collection_exists(collection):
            self.client.create_collection(
                collection_name=collection,
                vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
            )

    @staticmethod
    def _point_id(chunk_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))

    def upsert(self, records: list[dict[str, Any]], vectors: list[list[float]]) -> None:
        if len(records) != len(vectors):
            raise ValueError("record/vector count mismatch")
        points = []
        for record, vector in zip(records, vectors):
            if len(vector) != self.dimension:
                raise ValueError(f"Expected {self.dimension}-dimension embedding, got {len(vector)}")
            points.append(models.PointStruct(
                id=self._point_id(record["chunk_id"]), vector=vector,
                payload={k: record[k] for k in ("chunk_id", "source_id", "source_revision", "locator", "title", "text")},
            ))
        if points:
            self.client.upsert(collection_name=self.collection, points=points, wait=True)

    def search(self, query: str, top_k: int, source_ids: list[str] | None = None) -> list[SearchHit]:
        vector = self.embedding.embed([query], query=True)[0]
        query_filter = None
        if source_ids:
            query_filter = models.Filter(must=[models.FieldCondition(key="source_id", match=models.MatchAny(any=source_ids))])
        result = self.client.query_points(collection_name=self.collection, query=vector, query_filter=query_filter, limit=top_k, with_payload=True)
        hits = []
        for point in result.points:
            p = point.payload or {}
            hits.append(SearchHit(str(p["chunk_id"]), str(p["source_id"]), str(p["source_revision"]),
                                  str(p["locator"]), str(p["text"]), float(point.score or 0.0)))
        return hits


class HybridRRFIndex(Retriever):
    """Reciprocal Rank Fusion: top-20 dense + top-20 FTS5, final top-10."""

    adapter_id = "hybrid-dense-lexical-rrf"

    def __init__(
        self,
        dense: QdrantDenseIndex,
        lexical: SQLiteFTS5Index,
        rrf_k: int = 60,
        dense_candidates: int = 20,
        lexical_candidates: int = 20,
        source_metadata: list[dict[str, str]] | None = None,
    ) -> None:
        if rrf_k <= 0 or dense_candidates <= 0 or lexical_candidates <= 0:
            raise ValueError("RRF and candidate limits must be positive")
        self.dense = dense
        self.lexical = lexical
        self.rrf_k = rrf_k
        self.dense_candidates = dense_candidates
        self.lexical_candidates = lexical_candidates
        self.source_metadata = source_metadata or []
        self.last_search_filter: dict[str, Any] = {"applied": False, "reasons": []}

    @staticmethod
    def _normalized(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()

    @classmethod
    def explicit_metadata_scope(
        cls, query: str, source_metadata: list[dict[str, str]]
    ) -> tuple[list[str] | None, dict[str, Any]]:
        """Derive generic filters only from entity/class/version text in the query."""
        normalized_query = cls._normalized(query)
        matched: list[set[str]] = []
        reasons: list[dict[str, Any]] = []

        entity_alias_ids: dict[str, set[str]] = {}
        for source in source_metadata:
            title = str(source.get("title", ""))
            source_id = str(source.get("source_id", ""))
            aliases = {str(source.get("material_id", ""))}
            title_tokens = re.findall(r"[A-Za-z][A-Za-z0-9._-]*", title)
            # Source titles are frozen registry metadata. Treat only the leading
            # vendor name and model-like tokens containing digits as entities;
            # generic topic words such as "block", "SMART", or "endurance"
            # must never narrow retrieval.
            if title_tokens and not re.fullmatch(r"M\d+", title_tokens[0], re.IGNORECASE):
                aliases.add(title_tokens[0])
            aliases.update(
                token for token in title_tokens
                if len(token) >= 5 and any(char.isdigit() for char in token)
                and not re.fullmatch(r"\d+(?:\.\d+)+(?:[a-z])?", token, re.IGNORECASE)
            )
            for alias in aliases:
                norm_alias = cls._normalized(alias)
                # Manufacturer/model/family identifiers are scoped only when the
                # query contains the same explicit token or phrase.
                if norm_alias and (f" {norm_alias} " in f" {normalized_query} "):
                    entity_alias_ids.setdefault(norm_alias, set()).add(source_id)
        entity_ids = set.intersection(*entity_alias_ids.values()) if entity_alias_ids else set()
        if entity_ids:
            matched.append(entity_ids)
            reasons.append({"kind": "explicit_entity", "values": sorted(entity_alias_ids), "source_ids": sorted(entity_ids)})

        source_class_terms = {
            "datasheet": ("datasheet", "data sheet", "规格书", "数据手册"),
            "standard": ("standard", "标准", "nvme spec", "nvme specification"),
            "software": ("software documentation", "software doc", "软件文档", "api 文档", "linux 文档"),
        }
        for source_class, terms in source_class_terms.items():
            if any(
                (cls._normalized(term) and cls._normalized(term) in normalized_query)
                or (not cls._normalized(term) and term in query.lower())
                for term in terms
            ):
                ids = {str(s.get("source_id", "")) for s in source_metadata if s.get("source_class") == source_class}
                if ids:
                    matched.append(ids)
                    reasons.append({"kind": "explicit_source_class", "value": source_class, "source_ids": sorted(ids)})

        versions = set(re.findall(r"\b\d+(?:\.\d+)+(?:[a-z])?\b", query.lower()))
        if versions:
            candidates: set[str] = set()
            for source in source_metadata:
                source_text = cls._normalized(f"{source.get('title', '')} {source.get('source_id', '')} {source.get('source_revision', '')}")
                compact_source = source_text.replace(" ", "")
                if any("".join(re.findall(r"[a-z0-9]", version)) in compact_source for version in versions):
                    candidates.add(str(source.get("source_id", "")))
            if candidates:
                matched.append(candidates)
                reasons.append({"kind": "explicit_standard_version", "values": sorted(versions), "source_ids": sorted(candidates)})

        if not matched:
            return None, {"applied": False, "reasons": []}
        allowed = set.intersection(*matched)
        # Never convert incomplete catalog metadata into a zero-result query.
        if not allowed:
            return None, {"applied": False, "reasons": reasons, "fallback": "empty_intersection"}
        return sorted(allowed), {"applied": True, "reasons": reasons, "source_ids": sorted(allowed)}

    def search(self, query: str, top_k: int, source_ids: list[str] | None = None) -> list[SearchHit]:
        scoped_ids, filter_evidence = self.explicit_metadata_scope(query, self.source_metadata)
        if source_ids:
            scoped_ids = sorted(set(source_ids).intersection(scoped_ids)) if scoped_ids else source_ids
        self.last_search_filter = filter_evidence
        dense_hits = self.dense.search(query, self.dense_candidates, scoped_ids)
        lexical_hits = self.lexical.search(query, self.lexical_candidates, scoped_ids)
        by_id: dict[str, tuple[SearchHit, float]] = {}
        for hits in (dense_hits, lexical_hits):
            for rank, hit in enumerate(hits, start=1):
                old = by_id.get(hit.hit_id, (hit, 0.0))
                by_id[hit.hit_id] = (hit, old[1] + 1.0 / (self.rrf_k + rank))
        ordered = sorted(by_id.values(), key=lambda item: (-item[1], item[0].source_id, item[0].hit_id))[:top_k]
        return [SearchHit(hit.hit_id, hit.source_id, hit.source_revision, hit.locator, hit.text, score)
                for hit, score in ordered]
