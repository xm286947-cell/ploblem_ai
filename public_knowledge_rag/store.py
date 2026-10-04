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

    def import_source(self, title: str, source_uri: str | None, content: str, media_type: str, parser_id: str,
                      parser_version: str, chunks: list[Chunk]) -> tuple[str, str, bool]:
        source_key = (source_uri or title.strip()).lower().encode("utf-8")
        source_id = "src_" + hashlib.sha256(source_key).hexdigest()[:24]
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        revision_id = "rev_" + content_hash[:24]
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        with self.connect() as db:
            existing = db.execute("SELECT 1 FROM source_revisions WHERE source_id=? AND content_sha256=?", (source_id, content_hash)).fetchone()
            if existing:
                return source_id, revision_id, False
            db.execute("INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?)", (source_id, title, source_uri, "PUBLIC", now))
            db.execute("INSERT INTO source_revisions VALUES(?,?,?,?,?,?,?,?)", (source_id, revision_id, content_hash, content, media_type, parser_id, parser_version, now))
            for chunk in chunks:
                hit_id = "hit_" + hashlib.sha256(f"{source_id}:{revision_id}:{chunk.ordinal}".encode()).hexdigest()[:24]
                db.execute("INSERT INTO chunks VALUES(?,?,?,?,?,?)", (hit_id, source_id, revision_id, chunk.ordinal, chunk.locator, chunk.text))
        return source_id, revision_id, True

    def list_sources(self) -> list[dict[str, object]]:
        with self.connect() as db:
            rows = db.execute("""SELECT s.source_id,s.title,s.source_uri,s.created_at,r.revision_id,r.content_sha256,r.parser_id,r.parser_version,r.created_at revision_created_at
                FROM sources s JOIN source_revisions r ON r.source_id=s.source_id
                WHERE r.created_at=(SELECT MAX(r2.created_at) FROM source_revisions r2 WHERE r2.source_id=s.source_id)
                ORDER BY s.created_at DESC""").fetchall()
        return [dict(row) for row in rows]

    def get_source(self, source_id: str) -> dict[str, object] | None:
        with self.connect() as db:
            src = db.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
            if not src:
                return None
            revisions = db.execute("SELECT revision_id,content_sha256,media_type,parser_id,parser_version,created_at FROM source_revisions WHERE source_id=? ORDER BY created_at DESC", (source_id,)).fetchall()
        return {"source": dict(src), "revisions": [dict(row) for row in revisions]}

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
            row = db.execute("""SELECT c.hit_id citation_id,c.source_id,c.revision_id source_revision,c.locator,c.text,r.content_sha256
                FROM chunks c JOIN source_revisions r ON r.source_id=c.source_id AND r.revision_id=c.revision_id WHERE c.hit_id=?""", (citation_id,)).fetchone()
        return dict(row) if row else None
