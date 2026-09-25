"""Independent, evidence-first knowledge store with a stable API model."""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import ipaddress
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4

import httpx

from . import core

MAX_SOURCE_BYTES = 20 * 1024 * 1024


class RemoteSourceError(Exception):
    """The allowlisted server did not provide a usable document."""


def initialize(con: sqlite3.Connection):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS knowledge_sources(
      id TEXT PRIMARY KEY, title TEXT NOT NULL, publisher TEXT NOT NULL,
      official_url TEXT NOT NULL, version TEXT NOT NULL, source_type TEXT NOT NULL,
      filename TEXT NOT NULL, local_path TEXT NOT NULL, sha256 TEXT NOT NULL,
      page_count INTEGER NOT NULL, verify_status TEXT NOT NULL DEFAULT 'indexed',
      verified_by TEXT, verified_at TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS knowledge_passages(
      id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL REFERENCES knowledge_sources(id),
      page_number INTEGER NOT NULL, section TEXT NOT NULL DEFAULT '',
      text TEXT NOT NULL, extraction_method TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_knowledge_passages_source ON knowledge_passages(source_id,page_number);
    """)
    try:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(text, tokenize='trigram')")
    except sqlite3.OperationalError:
        # Older Windows SQLite builds can still use the bounded LIKE fallback.
        pass


def connect():
    con = core.connect()
    initialize(con)
    return con


def _pieces(text: str, max_chars: int = 800):
    """Keep literal source text and page boundary; only break at source lines."""
    chunk = ""
    for line in text.splitlines():
        if not line.strip():
            continue
        line = line.strip()
        if chunk and len(chunk) + len(line) + 1 > max_chars:
            yield chunk
            chunk = ""
        if len(line) > max_chars:
            if chunk:
                yield chunk
                chunk = ""
            for start in range(0, len(line), max_chars):
                yield line[start:start + max_chars]
            continue
        chunk = (chunk + "\n" + line).strip()
    if chunk:
        yield chunk


def ingest(filename: str, data: bytes, title: str, publisher: str,
           official_url: str = "", version: str = ""):
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".md", ".txt"}:
        raise ValueError("知识资料仅支持 PDF、Markdown 或文本")
    if not title.strip() or not publisher.strip():
        raise ValueError("标题和发布方不能为空")
    if official_url and urlparse(official_url).scheme not in {"http", "https"}:
        raise ValueError("原始链接仅支持 http/https")
    if suffix == ".pdf":
        pages = core.extract_pdf(data)
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise ValueError("文本资料需为 UTF-8 编码") from e
        pages = [(1, text, "text")]
    passages = [(number, piece, method) for number, text, method in pages for piece in _pieces(text)]
    if not passages:
        raise ValueError("资料没有可索引文字")
    source_id = uuid4().hex
    path = core.DATA / "knowledge" / f"{source_id}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    try:
        with connect() as con:
            con.execute("""INSERT INTO knowledge_sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (source_id, title.strip(), publisher.strip(), official_url, version.strip(),
                 suffix[1:], Path(filename).name, str(path), hashlib.sha256(data).hexdigest(),
                 len(pages), "indexed", None, None, core.now()))
            fts = con.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_fts'").fetchone()
            for number, piece, method in passages:
                cursor = con.execute("""INSERT INTO knowledge_passages(source_id,page_number,text,extraction_method)
                    VALUES (?,?,?,?)""", (source_id, number, piece, method))
                if fts:
                    con.execute("INSERT INTO knowledge_fts(rowid,text) VALUES (?,?)", (cursor.lastrowid, piece))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"source_id": source_id, "verify_status": "indexed", "passage_count": len(passages),
            "page_count": len(pages), "reused": False}


def fetch_official_source(url: str, title: str, publisher: str, version: str = "", client=None):
    """Fetch a configured official HTTPS host, then reuse the ordinary review pipeline."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    allow = {x.strip().lower() for x in os.environ.get("STORAGE_LIFE_SOURCE_HOSTS", "").split(",") if x.strip()}
    try:
        port = parsed.port
    except ValueError as e:
        raise ValueError("资料 URL 端口无效") from e
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or port not in (None, 443):
        raise ValueError("仅支持无账号信息的 HTTPS 官方资料 URL")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("不能从 IP 地址下载资料")
    if host == "localhost" or host.endswith(".local") or host not in allow:
        raise ValueError("资料域名未加入 STORAGE_LIFE_SOURCE_HOSTS 允许列表")
    if not title.strip() or not publisher.strip():
        raise ValueError("标题和发布方不能为空")
    owned = client is None
    if owned:
        client = httpx.Client(follow_redirects=False, timeout=20)
    try:
        with client.stream("GET", url, headers={"Accept": "application/pdf, text/plain, text/markdown"}) as response:
            if response.status_code != 200:
                raise RemoteSourceError(f"官方下载返回 HTTP {response.status_code}")
            if int(response.headers.get("content-length", "0") or 0) > MAX_SOURCE_BYTES:
                raise ValueError("远程资料超过 20 MB")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_SOURCE_BYTES:
                    raise ValueError("远程资料超过 20 MB")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
    except httpx.HTTPError as e:
        raise RemoteSourceError(f"官方下载失败：{type(e).__name__}") from e
    finally:
        if owned:
            client.close()
    suffix = Path(unquote(parsed.path)).suffix.lower()
    if content_type == "application/pdf" or (content_type == "application/octet-stream" and suffix == ".pdf"):
        suffix = ".pdf"
    elif content_type in {"text/markdown", "text/x-markdown"}:
        suffix = ".md"
    elif content_type == "text/plain":
        suffix = ".txt" if suffix not in {".md", ".txt"} else suffix
    elif suffix not in {".pdf", ".md", ".txt"}:
        raise ValueError("远程资料类型仅支持 PDF、Markdown、文本")
    filename = Path(unquote(parsed.path)).name or "official_source"
    filename = Path(filename).stem + suffix
    sha = hashlib.sha256(body).hexdigest()
    with connect() as con:
        previous = con.execute("""SELECT s.id,s.verify_status,s.page_count,COUNT(p.id) passage_count
            FROM knowledge_sources s JOIN knowledge_passages p ON p.source_id=s.id
            WHERE s.official_url=? AND s.sha256=? GROUP BY s.id ORDER BY s.created_at DESC LIMIT 1""",
            (url, sha)).fetchone()
    if previous:
        return {"source_id": previous["id"], "verify_status": previous["verify_status"],
                "passage_count": previous["passage_count"], "page_count": previous["page_count"], "reused": True}
    return ingest(filename, bytes(body), title, publisher, url, version)


def list_sources():
    with connect() as con:
        return core.rows(con, """SELECT id,title,publisher,official_url,version,source_type,filename,
            sha256,page_count,verify_status,verified_by,verified_at,created_at
            FROM knowledge_sources ORDER BY created_at DESC,id DESC""")


def get_source(source_id):
    with connect() as con:
        row = con.execute("SELECT * FROM knowledge_sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result.pop("local_path")
        result["passages"] = core.rows(con, "SELECT id,page_number,section,text,extraction_method FROM knowledge_passages WHERE source_id=? ORDER BY page_number,id", (source_id,))
        return result


def get_passage(passage_id: int):
    with connect() as con:
        row = con.execute("""SELECT p.id,p.source_id,p.page_number,p.section,p.text,p.extraction_method,
            s.title,s.publisher,s.official_url,s.version,s.filename,s.sha256,s.verify_status
            FROM knowledge_passages p JOIN knowledge_sources s ON s.id=p.source_id WHERE p.id=?""",
            (passage_id,)).fetchone()
        return dict(row) if row else None


def verify(source_id: str, status: str, reviewer: str):
    if status not in {"verified", "rejected"} or not reviewer.strip():
        raise ValueError("核验状态只能是 verified/rejected，且必须填写核验人")
    with connect() as con:
        cursor = con.execute("""UPDATE knowledge_sources SET verify_status=?, verified_by=?, verified_at=?
            WHERE id=?""", (status, reviewer.strip(), core.now(), source_id))
        if cursor.rowcount == 0:
            raise KeyError(source_id)
    return {"source_id": source_id, "verify_status": status}


def _source_hits(con, query: str, limit: int):
    terms = [x for x in re.findall(r"[\w]+|[\u4e00-\u9fff]+", query, re.UNICODE) if x]
    base = """SELECT p.id,p.source_id,p.page_number,p.section,p.text,p.extraction_method,
        s.title,s.publisher,s.official_url,s.version,s.filename,s.sha256,s.verify_status
        FROM knowledge_passages p JOIN knowledge_sources s ON s.id=p.source_id"""
    fts = con.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_fts'").fetchone()
    if fts and any(len(t) >= 3 for t in terms):
        expression = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms if len(t) >= 3)
        try:
            hits = core.rows(con, base + " JOIN knowledge_fts ON knowledge_fts.rowid=p.id "
                "WHERE s.verify_status='verified' AND knowledge_fts MATCH ? "
                "ORDER BY bm25(knowledge_fts),p.id LIMIT ?", (expression, limit))
            if hits:
                return hits
        except sqlite3.OperationalError:
            pass
    # Fallback also covers short Chinese queries and older SQLite builds.
    terms = terms[:5]
    if not terms:
        return []
    condition = " OR ".join("lower(p.text) LIKE ?" for _ in terms)
    return core.rows(con, base + " WHERE s.verify_status='verified' AND (" + condition + ") ORDER BY p.id LIMIT ?",
                     tuple("%" + t.lower() + "%" for t in terms) + (limit,))


def search(query: str, limit: int = 10):
    query = query.strip()
    if not query:
        return {"status": "no_evidence", "items": [], "answer": "请输入查询词"}
    limit = max(1, min(limit, 30))
    with connect() as con:
        passages = _source_hits(con, query, limit)
        specs = core.rows(con, """SELECT c.id,c.device_id,c.canonical_name,c.parameter_name,c.final_value,c.final_unit,
            c.source_page,c.source_section,c.source_text,d.vendor,d.model,d.device_type,
            s.id source_id,s.filename,s.original_url,s.publisher
            FROM candidates c JOIN devices d ON d.id=c.device_id JOIN sources s ON s.id=d.source_id
            WHERE c.verify_status='confirmed' ORDER BY c.id""")
    terms = [t.lower() for t in re.findall(r"[A-Za-z_0-9]+|[\u4e00-\u9fff]{2,}", query)]
    items = []
    for row in specs:
        hay = " ".join(str(row[k] or "") for k in ("canonical_name", "parameter_name", "source_text", "model", "device_type")).lower()
        if any(t in hay for t in terms):
            items.append({"kind": "confirmed_specification", "fact": f"{row['model']} {row['parameter_name']}: {row['final_value']} {row['final_unit']}".strip(),
                "evidence": {"source_id": row["source_id"], "title": row["filename"], "publisher": row["publisher"],
                    "page": row["source_page"], "section": row["source_section"], "original_text": row["source_text"],
                    "official_url": row["original_url"], "local_url": f"/api/sources/{row['source_id']}/pdf#page={row['source_page']}",
                    "verify_status": "confirmed"}})
    for row in passages:
        suffix = "#page=" + str(row["page_number"]) if row["filename"].lower().endswith(".pdf") else ""
        items.append({"kind": "verified_knowledge", "fact": row["text"],
            "evidence": {"source_id": row["source_id"], "passage_id": row["id"], "title": row["title"],
                "publisher": row["publisher"], "page": row["page_number"], "section": row["section"],
                "original_text": row["text"], "official_url": row["official_url"],
                "local_url": f"/api/v1/knowledge/sources/{row['source_id']}/file{suffix}",
                "sha256": row["sha256"], "verify_status": row["verify_status"]}})
    items = items[:limit]
    return {"status": "evidenced" if items else "no_evidence", "items": items,
            "answer": "找到以下已核验资料和正式规格" if items else "待验证：没有匹配的已核验来源或正式规格"}
