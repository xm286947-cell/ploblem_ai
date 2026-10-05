from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .contracts import Chunk, SearchHit


class Store:
    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "public_knowledge.sqlite3"
        self._init()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _init(self) -> None:
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY, title TEXT NOT NULL, source_uri TEXT,
                    source_class TEXT NOT NULL CHECK(source_class='PUBLIC'), created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_revisions (
                    source_id TEXT NOT NULL REFERENCES sources(source_id), revision_id TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL, content TEXT NOT NULL, media_type TEXT NOT NULL,
                    parser_id TEXT NOT NULL, parser_version TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(source_id, revision_id)
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    hit_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, revision_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL, locator TEXT NOT NULL, text TEXT NOT NULL,
                    FOREIGN KEY(source_id, revision_id) REFERENCES source_revisions(source_id, revision_id)
                );
                CREATE TABLE IF NOT EXISTS fixtures (
                    fixture_id TEXT PRIMARY KEY, request_json TEXT NOT NULL, response_json TEXT NOT NULL,
                    sha256 TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_revisions_source ON source_revisions(source_id, created_at);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(source_revisions)")}
            migrations = {
                "raw_sha256": "TEXT", "snapshot_bytes": "BLOB", "locator_ready": "INTEGER NOT NULL DEFAULT 1",
                "element_counts": "TEXT NOT NULL DEFAULT '{}'", "original_filename": "TEXT",
            }
            for name, sql_type in migrations.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE source_revisions ADD COLUMN {name} {sql_type}")
            db.execute("UPDATE source_revisions SET raw_sha256=content_sha256 WHERE raw_sha256 IS NULL")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_revision_raw_unique ON source_revisions(source_id,raw_sha256) WHERE raw_sha256 IS NOT NULL")

    def import_source(self, title: str, source_uri: str | None, content: str, media_type: str, parser_id: str,
                      parser_version: str, chunks: list[Chunk], *, raw_bytes: bytes | None = None,
                      locator_ready: bool = True, element_counts: dict[str, int] | None = None,
                      original_filename: str | None = None) -> tuple[str, str, bool]:
        source_key = (source_uri or title.strip()).lower().encode("utf-8")
        source_id = "src_" + hashlib.sha256(source_key).hexdigest()[:24]
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        raw_hash = hashlib.sha256(raw_bytes).hexdigest() if raw_bytes is not None else content_hash
        revision_id = "rev_" + raw_hash[:24]
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        with self.connect() as db:
            existing = db.execute("SELECT 1 FROM source_revisions WHERE source_id=? AND raw_sha256=?", (source_id, raw_hash)).fetchone()
            if existing:
                return source_id, revision_id, False
            db.execute("INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?)", (source_id, title, source_uri, "PUBLIC", now))
            db.execute("""INSERT INTO source_revisions
                (source_id,revision_id,content_sha256,content,media_type,parser_id,parser_version,created_at,
                 raw_sha256,snapshot_bytes,locator_ready,element_counts,original_filename)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (source_id, revision_id, content_hash, content, media_type, parser_id, parser_version, now,
                 raw_hash, raw_bytes, int(locator_ready), json.dumps(element_counts or {}, sort_keys=True), original_filename))
            for chunk in chunks:
                hit_id = "hit_" + hashlib.sha256(f"{source_id}:{revision_id}:{chunk.ordinal}".encode()).hexdigest()[:24]
                db.execute("INSERT INTO chunks VALUES(?,?,?,?,?,?)", (hit_id, source_id, revision_id, chunk.ordinal, chunk.locator, chunk.text))
        return source_id, revision_id, True

    def list_sources(self) -> list[dict[str, object]]:
        with self.connect() as db:
            rows = db.execute("""SELECT s.source_id,s.title,s.source_uri,s.created_at,r.revision_id,r.content_sha256,
                r.raw_sha256 source_sha256,r.media_type,r.parser_id,r.parser_version,r.locator_ready,r.element_counts,
                r.created_at revision_created_at
                FROM sources s JOIN source_revisions r ON r.source_id=s.source_id
                WHERE r.created_at=(SELECT MAX(r2.created_at) FROM source_revisions r2 WHERE r2.source_id=s.source_id)
                ORDER BY s.created_at DESC""").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["locator_ready"] = bool(item["locator_ready"])
            item["element_counts"] = json.loads(item["element_counts"] or "{}")
            result.append(item)
        return result

    def get_source(self, source_id: str) -> dict[str, object] | None:
        with self.connect() as db:
            src = db.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
            if not src:
                return None
            revisions = db.execute("""SELECT revision_id,content_sha256,raw_sha256,media_type,parser_id,parser_version,
                created_at,locator_ready,element_counts,original_filename,LENGTH(snapshot_bytes) snapshot_size
                FROM source_revisions WHERE source_id=? ORDER BY created_at DESC""", (source_id,)).fetchall()
        revision_data = []
        for row in revisions:
            value = dict(row)
            value["element_counts"] = json.loads(value["element_counts"] or "{}")
            value["locator_ready"] = bool(value["locator_ready"])
            revision_data.append(value)
        return {"source": dict(src), "revisions": revision_data}

    def get_snapshot(self, source_id: str, revision_id: str) -> tuple[bytes, str, str, str] | None:
        with self.connect() as db:
            row = db.execute("SELECT snapshot_bytes,media_type,original_filename,raw_sha256 FROM source_revisions WHERE source_id=? AND revision_id=?",
                             (source_id, revision_id)).fetchone()
        if not row or row["snapshot_bytes"] is None:
            return None
        return bytes(row["snapshot_bytes"]), row["media_type"], row["original_filename"] or "source", row["raw_sha256"]

    def search(self, query: str, top_k: int, source_ids: list[str] | None = None) -> list[SearchHit]:
        tokens = [token for token in query.replace("\n", " ").split() if len(token) >= 2]
        if not tokens:
            tokens = [query.strip()]
        where = "(" + " OR ".join("c.text LIKE ?" for _ in tokens) + ")"
        values: list[object] = [f"%{t}%" for t in tokens]
        if source_ids:
            where += " AND c.source_id IN (" + ",".join("?" for _ in source_ids) + ")"
            values.extend(source_ids)
        values.append(max(1, min(top_k, 50)))
        with self.connect() as db:
            rows = db.execute(f"SELECT c.hit_id,c.source_id,c.revision_id,c.locator,c.text FROM chunks c WHERE {where} ORDER BY c.source_id,c.revision_id,c.ordinal LIMIT ?", values).fetchall()
        hits = []
        for i, row in enumerate(rows):
            matched = sum(1 for token in tokens if token.lower() in row["text"].lower())
            hits.append(SearchHit(row["hit_id"], row["source_id"], row["revision_id"], row["locator"], row["text"], matched / len(tokens)))
        return hits

    def capture_fixture(self, request: dict[str, object], response: dict[str, object]) -> dict[str, object]:
        fixture_id = "fx_" + uuid.uuid4().hex[:24]
        request_json = json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        response_json = json.dumps(response, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256((request_json + "\n" + response_json).encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        with self.connect() as db:
            db.execute("INSERT INTO fixtures VALUES(?,?,?,?,?)", (fixture_id, request_json, response_json, digest, now))
        return {"fixture_id": fixture_id, "sha256": digest, "created_at": now, "request": request, "response": response}

    def get_fixture(self, fixture_id: str) -> dict[str, object] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM fixtures WHERE fixture_id=?", (fixture_id,)).fetchone()
        if not row:
            return None
        return {"fixture_id": row["fixture_id"], "request": json.loads(row["request_json"]), "response": json.loads(row["response_json"]), "sha256": row["sha256"], "created_at": row["created_at"]}

    def resolve(self, citation_id: str) -> dict[str, object] | None:
        with self.connect() as db:
            row = db.execute("""SELECT c.hit_id citation_id,c.source_id,c.revision_id source_revision,c.locator,c.text,
                r.content_sha256,r.raw_sha256 source_sha256,r.media_type,r.original_filename,r.locator_ready,
                r.element_counts,LENGTH(r.snapshot_bytes) snapshot_size
                FROM chunks c JOIN source_revisions r ON r.source_id=c.source_id AND r.revision_id=c.revision_id WHERE c.hit_id=?""", (citation_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["locator_ready"] = bool(result["locator_ready"])
        result["element_counts"] = json.loads(result["element_counts"] or "{}")
        return result
