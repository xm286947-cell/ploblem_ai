from __future__ import annotations

import hashlib
import re
from io import BytesIO
from pathlib import Path

import yaml

PARSER_VERSION = "md-v1"


def _escape_cell(value: object) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("|", "\\|")


def _table_to_markdown(table) -> str:
    rows = [[_escape_cell(c) for c in (row or [])] for row in (table or [])]
    rows = [row for row in rows if any(cell for cell in row)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header = rows[0]
    body = rows[1:]
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
    out.extend("| " + " | ".join(r) + " |" for r in body)
    return "\n".join(out)


def _looks_like_heading(line: str) -> bool:
    s = re.sub(r"\s+", " ", str(line or "")).strip()
    if not s or len(s) > 110:
        return False
    if re.fullmatch(r"[\d\s.\-/]+", s):
        return False
    letters = re.sub(r"[^A-Za-z]", "", s)
    if letters and len(letters) >= 4 and letters.upper() == letters:
        return True
    if re.match(r"^\d+(?:\.\d+){0,3}\s+[A-Z][A-Za-z0-9 /&()_-]+$", s):
        return True
    return False


def _text_to_markdown(text: str) -> str:
    lines = []
    for raw in str(text or "").splitlines():
        s = raw.rstrip()
        if not s.strip():
            lines.append("")
        elif _looks_like_heading(s):
            lines.append("### " + re.sub(r"\s+", " ", s).strip())
        else:
            lines.append(s)
    return "\n".join(lines).strip()


def build_markdown(data: bytes, pages):
    """Build page-addressable Markdown. Uses pdfplumber tables when available and falls back cleanly.

    Returns (document_markdown, md_pages, stats), where md_pages preserves the original
    `(page, text, extraction_method)` shape so existing read-plan/Agent code can consume it.
    """
    tables_by_page = {}
    table_count = 0
    try:
        import pdfplumber  # optional runtime enhancement; dependency is declared in V0.8.0
        wanted_pages = {int(p[0]) for p in pages}
        with pdfplumber.open(BytesIO(data)) as pdf:
            for idx, page in enumerate(pdf.pages, 1):
                if idx not in wanted_pages:
                    continue
                try:
                    tables = page.extract_tables() or []
                except Exception:
                    tables = []
                md_tables = [x for x in (_table_to_markdown(t) for t in tables) if x]
                if md_tables:
                    tables_by_page[idx] = md_tables
                    table_count += len(md_tables)
    except Exception:
        tables_by_page = {}

    page_docs, md_pages = [], []
    for page_no, text, method in pages:
        parts = [f"# Page {page_no}", f"<!-- extraction_method: {method} -->"]
        body = _text_to_markdown(text)
        if body:
            parts.extend(["", "## Page Text", body])
        md_tables = tables_by_page.get(page_no, [])
        if md_tables:
            parts.extend(["", "## Extracted Tables"])
            for i, table in enumerate(md_tables, 1):
                parts.extend(["", f"### Table {i}", table])
        page_md = "\n".join(parts).strip() + "\n"
        page_docs.append(page_md)
        md_pages.append((page_no, page_md, "markdown_" + method))
    document_md = "\n\n---\n\n".join(page_docs)
    return document_md, md_pages, {
        "parser_version": PARSER_VERSION,
        "page_count": len(md_pages),
        "table_count": table_count,
        "markdown_sha256": hashlib.sha256(document_md.encode("utf-8")).hexdigest(),
    }


def _first_match(pages, patterns):
    for page_no, text, _ in pages:
        for pat in patterns:
            m = re.search(pat, text, re.I | re.M)
            if m:
                value = (m.group(1) if m.lastindex else m.group(0)).strip()
                quote = re.sub(r"\s+", " ", m.group(0)).strip()
                return {"value": value, "page": page_no, "quote": quote, "confidence": 0.72}
    return {"value": "", "page": 0, "quote": "", "confidence": 0.0}


def heuristic_identity(pages, vendor: str = "", product_family: str = ""):
    """Conservative deterministic fallback for document identity only, never for spec values."""
    selected = list(pages[:8]) + list(pages[-3:] if len(pages) > 8 else [])
    document_number = _first_match(selected, [
        r"\b((?:DS|DS-SP)-\d{4,8})\b",
        r"\b(PM\d{3,6})\b",
        r"\b(?:Document\s*(?:No\.?|Number)|Doc\.?\s*No\.?)\s*[:#]?\s*([A-Z0-9][A-Z0-9_.\-/]{3,})",
        r"\b(\d{3}-\d{5})\b",
    ])
    revision = _first_match(selected, [
        r"\b((?:Rev(?:ision)?\.?\s*)[A-Z0-9][A-Z0-9.\-]*(?:MT)?)\b",
        r"\b(Rev\s+[A-Z0-9][A-Z0-9.\-]*)\b",
    ])
    revision_date = _first_match(selected, [
        r"\b(20\d{2}[-/.]\d{1,2}[-/.]\d{1,2})\b",
        r"\b((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+20\d{2})\b",
        r"\b(20\d{2}[-/.]\d{1,2})\b",
    ])
    status = _first_match(selected, [r"\b(Preliminary)\b", r"\b(Advanced Information)\b", r"\b(Released)\b", r"\b(Final)\b"])
    variant = _first_match(selected, [r"\b(J\s*Grade)\b", r"\b(Automotive)\b", r"\b(Industrial\+?)\b", r"\b(MT)\b"])
    all_text = "\n".join(x[1] for x in selected)
    ascii_letters = len(re.findall(r"[A-Za-z]", all_text))
    cjk = len(re.findall(r"[\u4e00-\u9fff]", all_text))
    language = "English" if ascii_letters >= cjk else "Chinese"
    return {
        "vendor": {"value": vendor, "page": 0, "quote": "", "confidence": 1.0 if vendor else 0.0},
        "product_family": {"value": product_family, "page": 0, "quote": "", "confidence": 1.0 if product_family else 0.0},
        "document_number": document_number,
        "revision": revision,
        "revision_date": revision_date,
        "document_status": status,
        "document_variant": variant,
        "language": {"value": language, "page": 0, "quote": "", "confidence": 0.55},
        "identity_source": "heuristic",
    }



CATALOG_PATH = Path(__file__).resolve().parents[1] / "config" / "document_catalog.yaml"


def catalog_status(identity: dict):
    """Compare an extracted document identity with the explicitly verified official snapshot catalog."""
    if not CATALOG_PATH.exists():
        return {"official_latest_status": "catalog_unavailable"}
    cfg = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8")) or {}
    docs = cfg.get("documents") or {}
    doc = identity.get("document_number") or {}
    rev = identity.get("revision") or {}
    doc_no = str(doc.get("value") if isinstance(doc, dict) else doc or "").strip()
    revision = str(rev.get("value") if isinstance(rev, dict) else rev or "").strip()
    entry = docs.get(doc_no)
    if not entry:
        return {"official_latest_status": "not_in_catalog", "catalog_checked_on": cfg.get("checked_on", "")}
    latest = str(entry.get("latest_revision") or "").strip()
    def norm_rev(x):
        return re.sub(r"(?i)^rev(?:ision)?\.?\s*", "", str(x or "")).replace(" ", "").upper()
    status = "matches_catalog_latest" if revision and norm_rev(revision) == norm_rev(latest) else "catalog_version_differs"
    if revision and latest and status != "matches_catalog_latest":
        try:
            status = "older_than_catalog" if natural_revision_key(revision) < natural_revision_key(latest) else "newer_or_branch_than_catalog"
        except Exception:
            pass
    return {
        "official_latest_status": status,
        "official_latest_revision": latest,
        "official_latest_date": entry.get("latest_date", ""),
        "catalog_checked_on": cfg.get("checked_on", ""),
        "catalog_source_url": entry.get("source_url", ""),
    }

def identity_key(identity: dict, sha256: str) -> str:
    def val(name):
        item = identity.get(name) or {}
        if isinstance(item, dict):
            return str(item.get("value") or "").strip()
        return str(item or "").strip()
    pieces = [val("vendor"), val("document_number"), val("document_variant"), val("revision"), val("language")]
    if not val("document_number"):
        pieces.insert(1, val("product_family"))
    normalized = "|".join(re.sub(r"\s+", " ", x).strip().casefold() for x in pieces)
    if not any(p for p in pieces[1:4]):
        normalized += "|sha256:" + sha256
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def natural_revision_key(value: str):
    raw = str(value or "").upper().replace("REVISION", "").replace("REV.", "").replace("REV", "").strip()
    parts = re.findall(r"\d+|[A-Z]+", raw)
    out = []
    for p in parts:
        out.append((0, int(p)) if p.isdigit() else (1, p))
    return tuple(out)
