from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from pypdf import PdfReader
from pypdf.errors import PdfReadError

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("STORAGE_LIFE_DATA_DIR", str(ROOT / "data"))).expanduser().resolve()
DB = DATA / "storage_life.sqlite3"
FIELDS = {
    "capacity": ("Capacity", r"(?:Capacity|容量)\s*[:：]\s*([\d.]+)\s*(GB|TB|Gb)"),
    "interface": ("Interface", r"(?:Interface|接口)\s*[:：]\s*([A-Za-z0-9 .+-]+)"),
    "tbw": ("TBW", r"\bTBW\s*[:：]\s*([\d.]+)\s*(TB|PB)"),
    "dwpd": ("DWPD", r"\bDWPD\s*[:：]\s*([\d.]+)"),
    "life_time_a": ("Device Life Time Estimation A", r"Device Life Time Estimation A\s*[:：]\s*([^\n]+)"),
    "pre_eol": ("PRE_EOL_INFO", r"PRE_EOL_INFO\s*[:：]\s*([^\n]+)"),
    "pe_cycles": ("P/E Cycle", r"P/E Cycles?\s*[:：]\s*([\d,]+)\s*(cycles?)?"),
    "page_size": ("Page Size", r"Page Size\s*[:：]\s*([\d.]+)\s*(KB|KiB)"),
    "ecc_requirement": ("ECC Requirement", r"ECC Requirement\s*[:：]\s*([^\n]+)"),
}


class ConfirmationConflict(ValueError):
    pass


class DuplicateDocumentError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def connect():
    DATA.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, filename TEXT, sha256 TEXT,
      local_path TEXT, original_url TEXT, publisher TEXT, page_count INTEGER, created_at TEXT);
    CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY, vendor TEXT, model TEXT,
      device_type TEXT CHECK(device_type IN ('SSD','eMMC','Raw NAND','NAND Flash','NOR Flash')), source_id TEXT REFERENCES sources(id));
    CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY, device_id TEXT REFERENCES devices(id),
      canonical_name TEXT, parameter_name TEXT, ai_value TEXT, ai_unit TEXT,
      final_value TEXT, final_unit TEXT, condition TEXT DEFAULT '', scope TEXT DEFAULT '', source_page INTEGER,
      source_section TEXT DEFAULT '', source_text TEXT, confidence REAL,
      extraction_method TEXT DEFAULT 'text',
      verify_status TEXT DEFAULT 'pending' CHECK(verify_status IN ('pending','confirmed','rejected')),
      verified_by TEXT, verified_at TEXT);
    CREATE TABLE IF NOT EXISTS links(id TEXT PRIMARY KEY, device_id TEXT REFERENCES devices(id),
      target_system TEXT, target_id TEXT, relation TEXT, created_at TEXT,
      UNIQUE(device_id,target_system,target_id,relation));
    CREATE TABLE IF NOT EXISTS candidate_evidence(id TEXT PRIMARY KEY, candidate_id TEXT REFERENCES candidates(id) ON DELETE CASCADE,
      source_page INTEGER, source_section TEXT DEFAULT '', source_text TEXT, confidence REAL,
      extraction_method TEXT DEFAULT 'agent_text', scope TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS candidate_evidence_provenance(evidence_id TEXT PRIMARY KEY REFERENCES candidate_evidence(id) ON DELETE CASCADE,
      source_id TEXT);
    CREATE TABLE IF NOT EXISTS document_models(id TEXT PRIMARY KEY, source_id TEXT REFERENCES sources(id),
      device_id TEXT REFERENCES devices(id), ai_model TEXT, final_model TEXT, scope TEXT DEFAULT '',
      source_page INTEGER, source_text TEXT, confidence REAL,
      verify_status TEXT DEFAULT 'pending' CHECK(verify_status IN ('pending','confirmed','rejected')),
      verified_by TEXT, verified_at TEXT, UNIQUE(source_id,ai_model,scope));
    CREATE TABLE IF NOT EXISTS final_reviews(id TEXT PRIMARY KEY, device_id TEXT REFERENCES devices(id),
      overall_status TEXT, summary TEXT, missing_fields_json TEXT DEFAULT '[]', findings_json TEXT DEFAULT '[]',
      created_at TEXT);
    CREATE TABLE IF NOT EXISTS final_review_corrections(id TEXT PRIMARY KEY,
      review_id TEXT REFERENCES final_reviews(id) ON DELETE CASCADE,
      device_id TEXT REFERENCES devices(id) ON DELETE CASCADE,
      candidate_id TEXT REFERENCES candidates(id) ON DELETE CASCADE,
      action TEXT, old_value TEXT, old_unit TEXT, old_condition TEXT, old_scope TEXT,
      new_value TEXT, new_unit TEXT, new_condition TEXT, new_scope TEXT,
      reason TEXT, applied INTEGER DEFAULT 0, apply_note TEXT DEFAULT '', created_at TEXT);
    CREATE TABLE IF NOT EXISTS candidate_review_history(id TEXT PRIMARY KEY,
      candidate_id TEXT REFERENCES candidates(id) ON DELETE CASCADE,
      device_id TEXT REFERENCES devices(id) ON DELETE CASCADE,
      version INTEGER, action TEXT, prior_status TEXT, new_status TEXT,
      ai_value TEXT, ai_unit TEXT, old_final_value TEXT, old_final_unit TEXT,
      old_condition TEXT, old_scope TEXT, new_final_value TEXT, new_final_unit TEXT,
      new_condition TEXT, new_scope TEXT, evidence_refs_json TEXT DEFAULT '[]',
      reviewed_by TEXT, reviewed_at TEXT, UNIQUE(candidate_id,version));
    CREATE TABLE IF NOT EXISTS parsed_documents(source_id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
      md_path TEXT, parser_version TEXT, markdown_sha256 TEXT, table_count INTEGER DEFAULT 0,
      parse_status TEXT DEFAULT 'ready', created_at TEXT);
    CREATE TABLE IF NOT EXISTS document_identities(id TEXT PRIMARY KEY, source_id TEXT UNIQUE REFERENCES sources(id) ON DELETE CASCADE,
      device_id TEXT REFERENCES devices(id) ON DELETE CASCADE, identity_key TEXT UNIQUE, document_number TEXT DEFAULT '',
      revision TEXT DEFAULT '', revision_date TEXT DEFAULT '', document_status TEXT DEFAULT '', document_variant TEXT DEFAULT '',
      language TEXT DEFAULT '', identity_source TEXT DEFAULT '', identity_json TEXT DEFAULT '{}', created_at TEXT);
    CREATE TABLE IF NOT EXISTS reviewed_specifications(id TEXT PRIMARY KEY, device_id TEXT REFERENCES devices(id) ON DELETE CASCADE,
      canonical_name TEXT, parameter_name TEXT, category TEXT, priority TEXT, value TEXT, unit TEXT, condition TEXT DEFAULT '',
      scope TEXT DEFAULT '', candidate_ids_json TEXT DEFAULT '[]', evidence_json TEXT DEFAULT '[]',
      review_status TEXT DEFAULT 'pending', created_at TEXT, updated_at TEXT);
    CREATE INDEX IF NOT EXISTS idx_reviewed_specs_device ON reviewed_specifications(device_id,category,priority,canonical_name);
    CREATE TABLE IF NOT EXISTS device_conclusions(device_id TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
      lifetime_summary TEXT DEFAULT '', diagnostic_summary TEXT DEFAULT '', software_recommendation TEXT DEFAULT '',
      risks_json TEXT DEFAULT '[]', missing_critical_fields_json TEXT DEFAULT '[]', key_specs_json TEXT DEFAULT '[]',
      conclusion_status TEXT DEFAULT 'insufficient', generator_version TEXT DEFAULT '', generated_at TEXT);
    CREATE TABLE IF NOT EXISTS extraction_runs(id TEXT PRIMARY KEY, device_id TEXT REFERENCES devices(id) ON DELETE CASCADE,
      source_id TEXT, extraction_mode TEXT, schema_valid INTEGER DEFAULT 1, model_calls INTEGER DEFAULT 0,
      unresolved_evidence_json TEXT DEFAULT '[]', review_required INTEGER DEFAULT 0, review_queue_json TEXT DEFAULT '[]',
      facts_json TEXT DEFAULT '[]', expected_fields_json TEXT DEFAULT '[]', searched_pages_json TEXT DEFAULT '[]',
      searched_sections_json TEXT DEFAULT '[]', searched_fields_json TEXT DEFAULT '{}', coverage_json TEXT DEFAULT '{}',
      coverage_layers_json TEXT DEFAULT '{}', document_analysis_json TEXT DEFAULT '{}', created_at TEXT);
    """)
    columns = {row[1] for row in con.execute("PRAGMA table_info(candidates)")}
    if "extraction_method" not in columns:
        con.execute("ALTER TABLE candidates ADD COLUMN extraction_method TEXT DEFAULT 'text'")
    if "scope" not in columns:
        con.execute("ALTER TABLE candidates ADD COLUMN scope TEXT DEFAULT ''")
    run_columns = {row[1] for row in con.execute("PRAGMA table_info(extraction_runs)")}
    if "facts_json" not in run_columns:
        con.execute("ALTER TABLE extraction_runs ADD COLUMN facts_json TEXT DEFAULT '[]'")
    if "expected_fields_json" not in run_columns:
        con.execute("ALTER TABLE extraction_runs ADD COLUMN expected_fields_json TEXT DEFAULT '[]'")
    for column, declaration in (
        ("searched_pages_json", "TEXT DEFAULT '[]'"),
        ("searched_sections_json", "TEXT DEFAULT '[]'"),
        ("searched_fields_json", "TEXT DEFAULT '{}'"),
        ("coverage_json", "TEXT DEFAULT '{}'"),
        ("coverage_layers_json", "TEXT DEFAULT '{}'"),
        ("document_analysis_json", "TEXT DEFAULT '{}'"),
    ):
        if column not in run_columns:
            con.execute(f"ALTER TABLE extraction_runs ADD COLUMN {column} {declaration}")
    # Older V0.5.x databases allowed only SSD/eMMC/Raw NAND. Rebuild the three dependent
    # tables once so NOR Flash and the user-facing NAND Flash name can coexist with legacy data.
    device_sql = (con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='devices'").fetchone() or [""])[0] or ""
    if "NOR Flash" not in device_sql or "NAND Flash" not in device_sql:
        con.execute("PRAGMA foreign_keys=OFF")
        con.executescript("""
        CREATE TABLE devices_v060(id TEXT PRIMARY KEY, vendor TEXT, model TEXT,
          device_type TEXT CHECK(device_type IN ('SSD','eMMC','Raw NAND','NAND Flash','NOR Flash')), source_id TEXT REFERENCES sources(id));
        INSERT INTO devices_v060 SELECT id,vendor,model,CASE WHEN device_type='Raw NAND' THEN 'NAND Flash' ELSE device_type END,source_id FROM devices;
        CREATE TABLE candidates_v060(id TEXT PRIMARY KEY, device_id TEXT REFERENCES devices_v060(id),
          canonical_name TEXT, parameter_name TEXT, ai_value TEXT, ai_unit TEXT, final_value TEXT, final_unit TEXT,
          condition TEXT DEFAULT '', scope TEXT DEFAULT '', source_page INTEGER, source_section TEXT DEFAULT '', source_text TEXT,
          confidence REAL, extraction_method TEXT DEFAULT 'text', verify_status TEXT DEFAULT 'pending'
          CHECK(verify_status IN ('pending','confirmed','rejected')), verified_by TEXT, verified_at TEXT);
        INSERT INTO candidates_v060 SELECT id,device_id,canonical_name,parameter_name,ai_value,ai_unit,final_value,final_unit,
          condition,scope,source_page,source_section,source_text,confidence,extraction_method,verify_status,verified_by,verified_at FROM candidates;
        CREATE TABLE links_v060(id TEXT PRIMARY KEY, device_id TEXT REFERENCES devices_v060(id),
          target_system TEXT, target_id TEXT, relation TEXT, created_at TEXT, UNIQUE(device_id,target_system,target_id,relation));
        INSERT INTO links_v060 SELECT * FROM links;
        DROP TABLE links; DROP TABLE candidates; DROP TABLE devices;
        ALTER TABLE devices_v060 RENAME TO devices;
        ALTER TABLE candidates_v060 RENAME TO candidates;
        ALTER TABLE links_v060 RENAME TO links;
        """)
        con.execute("PRAGMA foreign_keys=ON")
    return con


def rows(con, sql, args=()):
    return [dict(x) for x in con.execute(sql, args).fetchall()]


def extract_pdf(data: bytes, max_pages: int | None = None):
    from io import BytesIO
    try:
        reader = PdfReader(BytesIO(data))
    except (PdfReadError, ValueError) as e:
        raise ValueError("PDF 文件无法解析") from e
    pages = []
    source_pages = reader.pages if max_pages is None else reader.pages[:max(1, int(max_pages))]
    for i, page in enumerate(source_pages, 1):
        text = page.extract_text() or ""
        method = "text"
        if not text.strip() and len(page.images) > 0:
            text = ocr_pdf_page(data, i)
            method = "ocr"
        pages.append((i, text, method))
    if not any(text.strip() for _, text, _ in pages):
        raise ValueError("PDF 没有可识别文字；请检查扫描清晰度和 OCR 语言配置")
    return pages


def extract_pdf_pages(data: bytes, page_numbers):
    """Extract only selected 1-based PDF pages, preserving source page numbers."""
    from io import BytesIO
    try:
        reader = PdfReader(BytesIO(data))
    except (PdfReadError, ValueError) as e:
        raise ValueError("PDF 文件无法解析") from e
    wanted = sorted({int(n) for n in page_numbers if int(n) >= 1})
    pages = []
    for page_number in wanted:
        if page_number > len(reader.pages):
            continue
        page = reader.pages[page_number - 1]
        text = page.extract_text() or ""
        method = "text"
        if not text.strip() and len(page.images) > 0:
            text = ocr_pdf_page(data, page_number)
            method = "ocr"
        pages.append((page_number, text, method))
    if not pages or not any(text.strip() for _, text, _ in pages):
        raise ValueError("PDF 没有可识别文字；请检查扫描清晰度和 OCR 语言配置")
    return pages


def ocr_pdf_page(data: bytes, page_number: int):
    """OCR an individual blank-text PDF page; requires Poppler and Tesseract on PATH."""
    with tempfile.TemporaryDirectory() as folder:
        pdf = Path(folder) / "source.pdf"
        image = Path(folder) / "page"
        pdf.write_bytes(data)
        try:
            rendered = subprocess.run(
                ["pdftoppm", "-f", str(page_number), "-l", str(page_number),
                 "-r", "200", "-singlefile", "-png", str(pdf), str(image)],
                capture_output=True, timeout=45, check=False,
            )
        except FileNotFoundError as e:
            raise ValueError("图片型 PDF 需要 Poppler 的 pdftoppm") from e
        except subprocess.TimeoutExpired as e:
            raise ValueError("PDF 页面渲染超时") from e
        if rendered.returncode or not image.with_suffix(".png").exists():
            raise ValueError("PDF 页面渲染失败")
        try:
            result = subprocess.run(
                ["tesseract", str(image.with_suffix(".png")), "stdout", "-l", "eng"],
                capture_output=True, text=True, timeout=45, check=False,
            )
        except FileNotFoundError as e:
            raise ValueError("图片型 PDF 需要安装 Tesseract OCR 并加入 PATH") from e
        except subprocess.TimeoutExpired as e:
            raise ValueError("OCR 超时") from e
        if result.returncode:
            raise ValueError("OCR 识别失败；请确认已安装英文语言包")
        return result.stdout


def candidates_from_pages(pages):
    found = []
    for page, text, method in pages:
        condition = ""
        for line in text.splitlines():
            if re.search(r"Internal ECC\s+On\b", line, re.IGNORECASE):
                condition = "Internal ECC On"
            elif re.search(r"Internal ECC\s+Off\b", line, re.IGNORECASE):
                condition = "Internal ECC Off"
            for canonical, (label, pattern) in FIELDS.items():
                match = re.search(pattern, line, re.IGNORECASE)
                if not match:
                    continue
                value = match.group(1).strip().replace(",", "")
                unit = match.group(2).strip() if match.lastindex and match.lastindex >= 2 and match.group(2) else ""
                found.append(dict(canonical_name=canonical, parameter_name=label,
                                  ai_value=value, ai_unit=unit, source_page=page,
                                  source_text=line.strip(), confidence=0.35 if method == "ocr" else 0.65,
                                  extraction_method=method))
            variants = (
                ("capacity", "Capacity", r"(?:Density\s+is|◆)\s*(\d+\s*G(?:b|bit))\b", "", ""),
                ("pe_cycles", "P/E Cycle", r"\bP/E\s+cycles\s+with\s+ECC\s*:\s*(\d+\s*[Kk]?)\b", "cycles", "with ECC"),
                ("retention", "Data Retention", r"\bData\s+retention\s*:\s*(\d+)\s*(Years?)\b", None, ""),
                ("page_size", "Page Size", r"\bPage\s+Size\s*[:：]\s*(\d+\s*-\s*Byte\s*\+\s*\d+\s*-\s*Byte)", "", condition),
                ("ecc_requirement", "ECC Requirement", r"\b(\d+\s*bits?\s*/\s*\d+\s*bytes?)\b", "", "Internal ECC"),
            )
            for canonical, label, pattern, fixed_unit, variant_condition in variants:
                match = re.search(pattern, line, re.IGNORECASE)
                if not match:
                    continue
                value = re.sub(r"\s+", " ", match.group(1)).strip()
                unit = match.group(2) if fixed_unit is None and match.lastindex and match.lastindex >= 2 else fixed_unit or ""
                found.append(dict(canonical_name=canonical, parameter_name=label,
                                  ai_value=value, ai_unit=unit, condition=variant_condition,
                                  source_page=page, source_text=line.strip(),
                                  confidence=0.35 if method == "ocr" else 0.65,
                                  extraction_method=method))
    return found


def _merge_document_identity(primary, fallback, vendor, product_family):
    fields = ("document_number", "revision", "revision_date", "document_status", "document_variant", "language")
    out = {
        "vendor": {"value": vendor, "page": 0, "quote": "", "confidence": 1.0 if vendor else 0.0},
        "product_family": {"value": product_family, "page": 0, "quote": "", "confidence": 1.0 if product_family else 0.0},
    }
    for field in fields:
        a = (primary or {}).get(field) or {}
        b = (fallback or {}).get(field) or {}
        out[field] = a if str(a.get("value") or "").strip() else b
    out["identity_source"] = "agent+heuristic" if primary else "heuristic"
    out["analyzed_pages"] = list((primary or {}).get("analyzed_pages") or [])
    return out


def get_document_identity(device_id):
    import json
    from . import document_pipeline
    with connect() as con:
        row = con.execute("""SELECT i.*,s.sha256,s.filename,s.original_url,d.vendor,d.model AS product_family,d.device_type
          FROM document_identities i JOIN sources s ON s.id=i.source_id JOIN devices d ON d.id=i.device_id
          WHERE i.device_id=?""", (device_id,)).fetchone()
        if not row:
            return None
        peers = rows(con, """SELECT i.revision,i.revision_date,i.identity_key,d.id AS device_id
          FROM document_identities i JOIN devices d ON d.id=i.device_id
          WHERE lower(COALESCE(i.document_number,''))=lower(?) AND lower(COALESCE(i.document_variant,''))=lower(?)
            AND lower(COALESCE(i.language,''))=lower(?)""",
          (row["document_number"] or "", row["document_variant"] or "", row["language"] or "")) if row["document_number"] else []
    item = dict(row)
    try:
        item["identity"] = json.loads(item.pop("identity_json") or "{}")
    except Exception:
        item["identity"] = {}
    if peers:
        latest = max(peers, key=lambda x: (document_pipeline.natural_revision_key(x.get("revision")), str(x.get("revision_date") or "")))
        item["latest_status"] = "latest_in_library" if latest.get("device_id") == device_id else "older_in_library"
        item["latest_revision_in_library"] = latest.get("revision") or ""
    else:
        item["latest_status"] = "only_version_in_library"
        item["latest_revision_in_library"] = item.get("revision") or ""
    item.update(document_pipeline.catalog_status(item.get("identity") or {
        "document_number": {"value": item.get("document_number") or ""},
        "revision": {"value": item.get("revision") or ""},
    }))
    return item


def get_parsed_document(device_id, include_markdown=False):
    with connect() as con:
        row = con.execute("""SELECT p.*,d.id AS device_id,s.filename FROM parsed_documents p
          JOIN devices d ON d.source_id=p.source_id JOIN sources s ON s.id=p.source_id WHERE d.id=?""", (device_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    if include_markdown:
        path = Path(item.get("md_path") or "")
        item["markdown"] = path.read_text(encoding="utf-8") if path.exists() else ""
    return item


def import_document(filename, data, vendor, model, device_type, original_url="", publisher="", model_candidates=None, progress=None, document_identity_override=None):
    """Import one datasheet using the RC3 single-pass extraction path.

    PDF/Markdown, database, Reviewed Specification and UI contracts stay unchanged.  The
    only architectural change is that the formal AI fact extraction is one call, followed
    by deterministic contract adaptation/evidence resolution/review gating.
    """
    from . import ai, templates, document_pipeline
    import json

    def report(stage, percent, message):
        if progress:
            progress(stage, percent, message)

    report("validate", 6, "正在校验导入信息…")
    device_type = templates.normalize_device_type(device_type)
    vendor = templates.canonical_vendor(vendor)
    if device_type not in set(templates.device_types()):
        raise ValueError("不支持的器件类型")
    if Path(filename).suffix.lower() != ".pdf":
        raise ValueError("当前仅接收 PDF")
    if original_url and urlparse(original_url).scheme not in {"http", "https"}:
        raise ValueError("原始来源 URL 仅支持 http/https")

    sha = hashlib.sha256(data).hexdigest()
    with connect() as con:
        duplicate = con.execute("""SELECT d.id,s.id AS source_id FROM sources s JOIN devices d ON d.source_id=s.id
          WHERE s.sha256=? LIMIT 1""", (sha,)).fetchone()
    if duplicate:
        raise DuplicateDocumentError(f"相同 PDF 已导入，器件记录：{duplicate['id']}")

    report("parse_pdf", 14, "正在解析 PDF 文本和页码…")
    pages = extract_pdf(data)
    report("markdown", 24, "正在生成结构化 Markdown（保留页码、章节和表格）…")
    document_md, md_pages, md_stats = document_pipeline.build_markdown(data, pages)
    report("read_plan", 32, f"PDF/Markdown 解析完成，共 {len(md_pages)} 页；正在生成单次抽取阅读计划…")
    plan = templates.build_read_plan(md_pages, device_type, vendor)
    fallback_identity = document_pipeline.heuristic_identity(md_pages, vendor, model)

    # Generate the stable source id before extraction so evidence can carry provenance even
    # before the database transaction persists the source row.
    source_id, device_id = uuid4().hex, uuid4().hex
    extraction_meta = {
        "schema_valid": True, "model_calls": 0, "unresolved_evidence": [],
        "review_required": False, "review_queue": [], "facts": [], "expected_fields": [],
        "searched_pages": [], "searched_sections": [], "searched_fields": {},
        "coverage": {}, "coverage_layers": {}, "document_analysis": {},
    }
    agent_identity = None
    extracted_models = []

    if ai.configured():
        report("agent_extract", 42, "Agent 正在一次读取高价值 Markdown 页面，抽取文档身份、寿命、诊断和证据定位…")
        result = ai.extract_specification_once(md_pages, device_type, vendor, model, source_id=source_id)
        extracted = result["candidates"]
        analyzed_pages = result["analyzed_pages"]
        agent_identity = result.get("document_identity") or None
        extracted_models = result.get("models") or []
        extraction_meta = {
            "schema_valid": bool(result.get("schema_valid", False)),
            "model_calls": int(result.get("model_calls", 1)),
            "unresolved_evidence": list(result.get("unresolved_evidence") or []),
            "review_required": bool(result.get("review_required", False)),
            "review_queue": list(result.get("review_queue") or []),
            "facts": list(result.get("facts") or []),
            "expected_fields": list(result.get("expected_fields") or []),
            "searched_pages": list(result.get("searched_pages") or []),
            "searched_sections": list(result.get("searched_sections") or []),
            "searched_fields": dict(result.get("searched_fields") or {}),
            "coverage": dict(result.get("coverage") or {}),
            "coverage_layers": dict(result.get("coverage_layers") or {}),
            "document_analysis": dict(result.get("document_analysis") or {}),
        }
        extraction_mode = "agent_single_pass_template" if templates.supported_vendor_template(vendor, device_type) else "agent_single_pass_generic"
        report("resolve_evidence", 72, f"单次抽取完成，共 {len(extracted)} 个已解析候选；正在执行确定性证据解析与校验…")
    elif os.environ.get("STORAGE_LIFE_ENABLE_RULE_FALLBACK", "").strip().lower() in {"1", "true", "yes", "on"}:
        report("rule_extract", 48, "已显式启用规则兜底，正在从原始分页文本提取候选参数…")
        extracted = candidates_from_pages(pages)
        analyzed_pages = [number for number, _, _ in pages]
        extraction_mode = "rules_opt_in"
    else:
        report("agent_unconfigured", 48, "Agent 未配置，将保存 PDF/Markdown 并保留寿命/诊断字段为待人工补充。")
        extracted, analyzed_pages = [], []
        extraction_mode = "agent_unconfigured"

    if not extraction_meta["coverage"]:
        from .coverage import IDENTITY_FIELDS, compute_coverage
        expected = [*IDENTITY_FIELDS, *(x["canonical_name"] for x in ai.expected_fields(device_type))]
        extraction_meta["expected_fields"] = list(dict.fromkeys(expected))
        extraction_meta["searched_pages"] = list(analyzed_pages)
        extraction_meta["coverage"] = compute_coverage(
            device_type=device_type,
            facts=extraction_meta["facts"],
            searched_pages=extraction_meta["searched_pages"],
            searched_fields=extraction_meta["searched_fields"],
            expected_fields=extraction_meta["expected_fields"],
        )
        extraction_meta["coverage_layers"] = extraction_meta["coverage"]["layers"]
        extraction_meta["document_analysis"] = {
            "document_identity": {}, "valid_part_numbers": [], "facts": extraction_meta["facts"],
            "searched_pages": extraction_meta["searched_pages"],
            "searched_sections": extraction_meta["searched_sections"],
            "coverage": extraction_meta["coverage"],
        }

    # Document identity is part of the same formal extraction call.  Conservative local
    # heuristics remain a fallback and user-confirmed form values remain highest priority.
    identity = _merge_document_identity(agent_identity, fallback_identity, vendor, model)
    overrides = document_identity_override or {}
    changed = False
    for field in ("document_number", "revision", "revision_date", "document_variant", "document_status", "language"):
        value = str(overrides.get(field) or "").strip()
        if value:
            identity[field] = {"value": value, "page": 0, "quote": "", "confidence": 1.0, "confirmed_by_user": True}
            changed = True
    if changed:
        identity["identity_source"] = "user_confirmed+" + str(identity.get("identity_source") or "extracted")
    identity_key = document_pipeline.identity_key(identity, sha)
    with connect() as con:
        duplicate_identity = con.execute("SELECT device_id FROM document_identities WHERE identity_key=? LIMIT 1", (identity_key,)).fetchone()
    if duplicate_identity:
        raise DuplicateDocumentError(f"同一规格书版本已导入，器件记录：{duplicate_identity['device_id']}")

    # Prefer explicit user-confirmed/pre-identified part-number candidates, but retain
    # additional source-grounded part numbers from the single-pass result without duplicates.
    merged_models = []
    seen_models = set()
    for item in list(model_candidates or []) + list(extracted_models):
        key = (str(item.get("value") or "").strip(), str(item.get("scope") or "").strip())
        if not key[0] or key in seen_models:
            continue
        seen_models.add(key); merged_models.append(item)

    extraction_meta["document_analysis"]["document_identity"] = identity
    extraction_meta["document_analysis"]["valid_part_numbers"] = merged_models

    report("persist", 82, "正在保存 PDF、Markdown、文档身份、型号候选、参数证据和抽取审计信息…")
    file_path = DATA / "documents" / f"{source_id}.pdf"
    md_path = DATA / "parsed" / source_id / "document.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(data)
    md_path.write_text(document_md, encoding="utf-8")
    try:
        with connect() as con:
            con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",
                        (source_id, Path(filename).name, sha, str(file_path), original_url, publisher, len(pages), now()))
            con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", (device_id, vendor, model, device_type, source_id))
            con.execute("INSERT INTO parsed_documents VALUES (?,?,?,?,?,?,?)",
                        (source_id, str(md_path), md_stats["parser_version"], md_stats["markdown_sha256"], md_stats["table_count"], "ready", now()))

            def iv(name):
                x = identity.get(name) or {}
                return str(x.get("value") or "").strip() if isinstance(x, dict) else str(x or "").strip()

            con.execute("""INSERT INTO document_identities
              (id,source_id,device_id,identity_key,document_number,revision,revision_date,document_status,document_variant,language,identity_source,identity_json,created_at)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (uuid4().hex, source_id, device_id, identity_key, iv("document_number"), iv("revision"), iv("revision_date"),
               iv("document_status"), iv("document_variant"), iv("language"), identity.get("identity_source", ""),
               json.dumps(identity, ensure_ascii=False), now()))

            for item in merged_models:
                ai_model = str(item.get("value") or "").strip(); quote = str(item.get("quote") or "").strip()
                if not ai_model or not quote:
                    continue
                try:
                    page = int(item.get("page") or 0); confidence = max(0.0, min(1.0, float(item.get("confidence") or 0)))
                except (TypeError, ValueError):
                    page, confidence = 0, 0.0
                con.execute("""INSERT OR IGNORE INTO document_models
                  (id,source_id,device_id,ai_model,scope,source_page,source_text,confidence) VALUES (?,?,?,?,?,?,?,?)""",
                  (uuid4().hex, source_id, device_id, ai_model, str(item.get("scope") or "").strip()[:300], page, quote[:1200], confidence))

            for c in extracted:
                candidate_id = uuid4().hex
                con.execute("""INSERT INTO candidates(id,device_id,canonical_name,parameter_name,ai_value,ai_unit,
                condition,scope,source_page,source_section,source_text,confidence,extraction_method) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (candidate_id, device_id, c["canonical_name"], c["parameter_name"], c["ai_value"], c["ai_unit"],
                 c.get("condition", ""), c.get("scope", ""), c["source_page"], c.get("source_section", ""), c["source_text"], c["confidence"], c["extraction_method"]))
                evidence = c.get("evidence") or [{"source_id": c.get("source_id") or source_id, "source_page": c["source_page"], "source_section": c.get("source_section", ""),
                    "source_text": c["source_text"], "confidence": c["confidence"], "extraction_method": c["extraction_method"], "scope": c.get("scope", "")}]
                for ev in evidence:
                    evidence_id = uuid4().hex
                    con.execute("INSERT INTO candidate_evidence VALUES (?,?,?,?,?,?,?,?)",
                      (evidence_id, candidate_id, ev.get("source_page"), ev.get("source_section", ""), ev.get("source_text", ""),
                       ev.get("confidence", 0), ev.get("extraction_method", "agent_single_pass"), ev.get("scope", "")))
                    con.execute("INSERT INTO candidate_evidence_provenance(evidence_id,source_id) VALUES (?,?)",
                                (evidence_id, ev.get("source_id") or source_id))

            con.execute("""INSERT INTO extraction_runs
              (id,device_id,source_id,extraction_mode,schema_valid,model_calls,unresolved_evidence_json,review_required,review_queue_json,
               facts_json,expected_fields_json,searched_pages_json,searched_sections_json,searched_fields_json,coverage_json,
               coverage_layers_json,document_analysis_json,created_at)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (uuid4().hex, device_id, source_id, extraction_mode, 1 if extraction_meta["schema_valid"] else 0,
               extraction_meta["model_calls"], json.dumps(extraction_meta["unresolved_evidence"], ensure_ascii=False),
               1 if extraction_meta["review_required"] else 0, json.dumps(extraction_meta["review_queue"], ensure_ascii=False),
               json.dumps(extraction_meta["facts"], ensure_ascii=False),
               json.dumps(extraction_meta["expected_fields"], ensure_ascii=False),
               json.dumps(extraction_meta["searched_pages"], ensure_ascii=False),
               json.dumps(extraction_meta["searched_sections"], ensure_ascii=False),
               json.dumps(extraction_meta["searched_fields"], ensure_ascii=False),
               json.dumps(extraction_meta["coverage"], ensure_ascii=False),
               json.dumps(extraction_meta["coverage_layers"], ensure_ascii=False),
               json.dumps(extraction_meta["document_analysis"], ensure_ascii=False), now()))
    except Exception:
        file_path.unlink(missing_ok=True)
        md_path.unlink(missing_ok=True)
        try:
            md_path.parent.rmdir()
        except OSError:
            pass
        raise

    rebuild_reviewed_specifications(device_id)
    report("completed", 100, "导入完成：单次事实抽取、证据解析、文档身份和寿命/诊断候选已保存。")
    return {
        "device_id": device_id, "source_id": source_id, "candidate_count": len(extracted),
        "model_candidate_count": len(list_models(device_id)), "extraction_mode": extraction_mode,
        "analyzed_pages": analyzed_pages, "planned_pages": [x["page"] for x in plan],
        "template": templates.template_summary(device_type, vendor),
        "ocr_pages": [number for number, _, method in pages if method == "ocr"],
        "document_identity": get_document_identity(device_id), "markdown": md_stats, "agent_input_format": "markdown",
        "schema_valid": extraction_meta["schema_valid"], "model_calls": extraction_meta["model_calls"],
        "unresolved_evidence": extraction_meta["unresolved_evidence"],
        "review_required": extraction_meta["review_required"], "review_queue": extraction_meta["review_queue"],
        "fact_count": len(extraction_meta["facts"]),
        "expected_field_count": len(extraction_meta["expected_fields"]),
        "searched_pages": extraction_meta["searched_pages"],
        "searched_sections": extraction_meta["searched_sections"],
        "coverage": extraction_meta["coverage"],
        "coverage_layers": extraction_meta["coverage_layers"],
    }



def get_extraction_run(device_id):
    """Return the latest deterministic extraction/review-gate audit record."""
    import json
    with connect() as con:
        row = con.execute("SELECT * FROM extraction_runs WHERE device_id=? ORDER BY created_at DESC LIMIT 1", (device_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["schema_valid"] = bool(item.get("schema_valid"))
    item["review_required"] = bool(item.get("review_required"))
    item["unresolved_evidence"] = json.loads(item.pop("unresolved_evidence_json") or "[]")
    item["review_queue"] = json.loads(item.pop("review_queue_json") or "[]")
    item["facts"] = json.loads(item.pop("facts_json", "[]") or "[]")
    item["expected_fields"] = json.loads(item.pop("expected_fields_json", "[]") or "[]")
    item["searched_pages"] = json.loads(item.pop("searched_pages_json", "[]") or "[]")
    item["searched_sections"] = json.loads(item.pop("searched_sections_json", "[]") or "[]")
    item["searched_fields"] = json.loads(item.pop("searched_fields_json", "{}") or "{}")
    item["coverage"] = json.loads(item.pop("coverage_json", "{}") or "{}")
    item["coverage_layers"] = json.loads(item.pop("coverage_layers_json", "{}") or "{}")
    item["document_analysis"] = json.loads(item.pop("document_analysis_json", "{}") or "{}")
    return item


def list_models(device_id):
    with connect() as con:
        return rows(con, """SELECT * FROM document_models WHERE device_id=?
          ORDER BY CASE verify_status WHEN 'confirmed' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
                   source_page,ai_model""", (device_id,))


def verify_model(model_id, status, value, by, scope=None):
    if status not in {"confirmed", "rejected"} or not str(by or "").strip():
        raise ValueError("需选择确认或驳回，并填写核对人")
    with connect() as con:
        item = con.execute("SELECT * FROM document_models WHERE id=?", (model_id,)).fetchone()
        if not item:
            raise KeyError(model_id)
        final_model = str(value if value is not None else item["ai_model"]).strip()
        final_scope = item["scope"] if scope is None else str(scope).strip()
        if status == "confirmed" and not final_model:
            raise ValueError("确认型号不能为空")
        con.execute("""UPDATE document_models SET verify_status=?,final_model=?,scope=?,verified_by=?,verified_at=?
          WHERE id=?""", (status, final_model, final_scope, str(by).strip(), now(), model_id))
    return {"id": model_id, "verify_status": status}


def _compact_match(text):
    return re.sub(r"[^A-Za-z0-9.+/%-]+", "", str(text or ""))


def _value_supported_by_candidate(con, candidate_id, value):
    """Final Review may only rewrite a value when the proposed text already exists in this candidate's evidence."""
    proposed = _compact_match(value)
    if not proposed:
        return True
    evidence = con.execute("SELECT source_text FROM candidate_evidence WHERE candidate_id=?", (candidate_id,)).fetchall()
    if not evidence:
        row = con.execute("SELECT source_text FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        evidence = [row] if row else []
    return any(proposed in _compact_match(row[0]) for row in evidence if row and row[0])


def save_final_review(device_id, review):
    """Save Final Review and safely apply evidence-grounded corrections to pending candidates.

    AI corrections never confirm a specification. Human-confirmed candidates are never overwritten.
    The original extraction remains in ai_value/ai_unit and every attempted correction is audited.
    """
    import json
    review_id = uuid4().hex
    corrections = review.get("corrections") or []
    with connect() as con:
        if not con.execute("SELECT 1 FROM devices WHERE id=?", (device_id,)).fetchone():
            raise KeyError(device_id)
        con.execute("""INSERT INTO final_reviews VALUES (?,?,?,?,?,?,?)""",
          (review_id, device_id, review.get("overall_status", "attention_required"),
           str(review.get("summary") or ""), json.dumps(review.get("missing_fields") or [], ensure_ascii=False),
           json.dumps(review.get("findings") or [], ensure_ascii=False), now()))
        for item in corrections[:40]:
            candidate_id = str(item.get("candidate_id") or "")
            candidate = con.execute("SELECT * FROM candidates WHERE id=? AND device_id=?", (candidate_id, device_id)).fetchone()
            if not candidate:
                continue
            action = str(item.get("action") or "manual_check")
            old_value = candidate["final_value"] if candidate["final_value"] is not None else candidate["ai_value"]
            old_unit = candidate["final_unit"] if candidate["final_unit"] is not None else candidate["ai_unit"]
            old_condition = candidate["condition"] or ""
            old_scope = candidate["scope"] or ""
            new_value = str(item.get("proposed_value") if item.get("proposed_value") is not None else old_value).strip()
            new_unit = str(item.get("proposed_unit") if item.get("proposed_unit") is not None else old_unit).strip()
            new_condition = str(item.get("proposed_condition") if item.get("proposed_condition") is not None else old_condition).strip()
            new_scope = str(item.get("proposed_scope") if item.get("proposed_scope") is not None else old_scope).strip()
            reason = str(item.get("reason") or "").strip()[:1000]
            applied = 0
            apply_note = "仅提示人工检查"
            if action == "update":
                if candidate["verify_status"] == "confirmed":
                    apply_note = "已人工确认，AI 不覆盖，仅保留修正建议"
                elif candidate["verify_status"] == "rejected":
                    apply_note = "候选已驳回，不自动修正"
                elif new_value and not _value_supported_by_candidate(con, candidate_id, new_value):
                    apply_note = "建议值未在原候选证据中直接出现，未自动应用"
                else:
                    con.execute("""UPDATE candidates SET final_value=?,final_unit=?,condition=?,scope=? WHERE id=?""",
                                (new_value, new_unit, new_condition, new_scope, candidate_id))
                    applied = 1
                    apply_note = "已修正待确认候选，仍需人工确认"
            con.execute("""INSERT INTO final_review_corrections
              (id,review_id,device_id,candidate_id,action,old_value,old_unit,old_condition,old_scope,
               new_value,new_unit,new_condition,new_scope,reason,applied,apply_note,created_at)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (uuid4().hex, review_id, device_id, candidate_id, action, old_value, old_unit, old_condition, old_scope,
               new_value, new_unit, new_condition, new_scope, reason, applied, apply_note, now()))
    rebuild_reviewed_specifications(device_id)
    return get_final_review(device_id)


def get_final_review(device_id):
    import json
    with connect() as con:
        row = con.execute("SELECT * FROM final_reviews WHERE device_id=? ORDER BY created_at DESC LIMIT 1",
                          (device_id,)).fetchone()
        if not row:
            return None
        correction_rows = rows(con, """SELECT candidate_id,action,old_value,old_unit,old_condition,old_scope,
          new_value,new_unit,new_condition,new_scope,reason,applied,apply_note,created_at
          FROM final_review_corrections WHERE review_id=? ORDER BY created_at,id""", (row["id"],))
    result = dict(row)
    result["missing_fields"] = json.loads(result.pop("missing_fields_json") or "[]")
    result["findings"] = json.loads(result.pop("findings_json") or "[]")
    for item in correction_rows:
        item["applied"] = bool(item["applied"])
    result["corrections"] = correction_rows
    result["applied_correction_count"] = sum(1 for x in correction_rows if x["applied"])
    return result


def _scope_tokens(text):
    stop = {"family","product","all","models","model","parts","part","grade","variant","device","series"}
    return {x.lower() for x in re.findall(r"[A-Za-z0-9.]+", str(text or "")) if len(x) >= 2 and x.lower() not in stop}


def _is_common_scope(scope, product_family):
    raw = str(scope or "").strip()
    if not raw:
        return True
    compact = re.sub(r"[^a-z0-9]+", "", raw.lower())
    family = re.sub(r"[^a-z0-9]+", "", str(product_family or "").lower())
    if compact in {"all", "allmodels", "allparts", "productfamily", "familywide", "common"}:
        return True
    return bool(family and (compact == family or family in compact))


def _model_matches_scope(scope, model):
    scope_raw = str(scope or "")
    model_name = str(model.get("final_model") or model.get("ai_model") or "")
    model_scope = str(model.get("scope") or "")
    ns = re.sub(r"[^a-z0-9]+", "", scope_raw.lower())
    nm = re.sub(r"[^a-z0-9]+", "", model_name.lower())
    if ns and nm and (nm in ns or ns in nm):
        return True
    st = _scope_tokens(scope_raw)
    mt = _scope_tokens(model_scope)
    # Shared discriminators such as 3.3V / 1.8V / I/J grade are useful when the scope names a variant rather than a full PN.
    return bool(st and mt and st.intersection(mt))


def _collapse_family_specs(items):
    """Collapse presentation-equivalent candidates while preserving all evidence.

    This is intentionally a presentation-layer merge: raw candidate rows stay untouched so
    users can still inspect/review every extraction when needed.
    """
    groups = {}
    for item in items:
        key = (
            item.get("canonical_name") or "",
            re.sub(r"\s+", " ", str(item.get("display_value") or "").strip().lower()),
            re.sub(r"\s+", " ", str(item.get("display_condition") or "").strip().lower()),
            re.sub(r"\s+", " ", str(item.get("display_scope") or "").strip().lower()),
        )
        bucket = groups.setdefault(key, [])
        bucket.append(item)
    out = []
    for bucket in groups.values():
        base = dict(bucket[0])
        candidate_ids, evidence = [], []
        seen_ev = set()
        for item in bucket:
            candidate_ids.append(item.get("id"))
            evs = list(item.get("evidence") or [])
            if not evs:
                evs = [{
                    "source_page": item.get("source_page"),
                    "source_section": item.get("source_section") or "",
                    "source_text": item.get("source_text") or "",
                }]
            for ev in evs:
                ek = (ev.get("source_id") or "", ev.get("source_page"), ev.get("source_section") or "", ev.get("source_text") or "")
                if ek in seen_ev:
                    continue
                seen_ev.add(ek)
                evidence.append(ev)
        base["candidate_ids"] = [x for x in candidate_ids if x]
        base["evidence"] = evidence
        base["evidence_count"] = len(evidence)
        base["duplicate_count"] = len(bucket)
        out.append(base)
    return out



REVIEWED_FIELD_GROUPS = {
    "NAND Flash": [
        ("写入寿命（Write Endurance）", ["cell_type", "capacity", "pe_cycles", "retention", "page_size", "pages_per_block", "block_size", "block_count"]),
        ("诊断能力（Diagnostics）", ["ecc_capability", "internal_ecc", "ecc_status", "factory_bad_block", "runtime_bad_block", "minimum_valid_blocks", "bad_block_mark", "bad_block_mark_location", "program_fail", "erase_fail", "status_register", "read_retry"]),
        ("写入约束（Write Constraints）", ["program_time", "erase_time", "operating_temperature"]),
    ],
    "NOR Flash": [
        ("写入寿命（Write Endurance）", ["pe_cycles", "retention", "page_size", "erase_granularity", "operating_temperature"]),
        ("诊断能力（Diagnostics）", ["status_register", "program_fail", "erase_fail", "error_flag", "ecc_status", "lifetime_counter"]),
        ("写入约束（Write Constraints）", ["program_time", "erase_time"]),
    ],
    "eMMC": [
        ("写入寿命（Write Endurance）", ["cell_type", "capacity", "default_user_area_type", "enhanced_user_data_area", "enhanced_area_cell_type", "pe_cycles", "write_reliability"]),
        ("诊断能力（Diagnostics）", ["ext_csd_health_report", "life_time_a", "life_time_b", "pre_eol", "bkops", "error_reporting", "vendor_health_report", "plp"]),
    ],
    "SSD": [
        ("写入寿命（Write Endurance）", ["cell_type", "capacity", "tbw", "dwpd", "endurance_class", "over_provisioning", "warranty_write_limit", "plp"]),
        ("诊断能力（Diagnostics）", ["smart_health", "percentage_used", "data_units_written", "available_spare", "spare_threshold", "media_errors", "critical_warning", "temperature", "unsafe_shutdowns", "error_log", "wear_level_remaining"]),
    ],
}



def _runtime_emmc_contract_fields():
    try:
        from .runtime_domain_strategy import EMMC_ANALYSIS_FIELDS
        return tuple(EMMC_ANALYSIS_FIELDS)
    except Exception:
        return ()


def _runtime_emmc_groups():
    try:
        from .runtime_domain_strategy import EMMC_ATOMIC_GROUPS
    except Exception:
        return []
    labels = {
        "media_endurance": "介质与寿命（Media / Endurance）",
        "partition_modes": "分区与存储模式（Partition / Storage Mode）",
        "standard_health": "标准健康诊断（Standard Health）",
        "vendor_health": "厂商健康诊断（Vendor Health）",
        "management_reliability": "管理与可靠性（Management / Reliability）",
    }
    out=[]
    for name, fields in EMMC_ATOMIC_GROUPS:
        if name == "document_identity":
            continue
        out.append((labels.get(name, name), list(fields)))
    return out


def _runtime_emmc_active(items):
    runtime_fields=set(_runtime_emmc_contract_fields())
    present={x.get("canonical_name") for x in (items or [])}
    return bool(runtime_fields and present & runtime_fields)


def _reviewed_groups_for(device_type, items=None):
    if device_type == "eMMC" and _runtime_emmc_active(items):
        return _runtime_emmc_groups()
    return REVIEWED_FIELD_GROUPS.get(device_type, [])


def _critical_fields_for(device_type, items):
    if device_type == "eMMC" and _runtime_emmc_active(items):
        return ["device_life_time_est_typ_a", "device_life_time_est_typ_b", "pre_eol_info"]
    return list(CONCLUSION_CRITICAL_FIELDS.get(device_type, []))


def _lifetime_fields_for(device_type, items):
    if device_type == "eMMC" and _runtime_emmc_active(items):
        return {"native_nand_type", "pe_cycle", "data_retention", "endurance_condition", "partition_storage_mode", "enhanced_user_data_area", "default_user_data_area"}
    return set(CONCLUSION_LIFETIME_FIELDS.get(device_type, set()))


def _diagnostic_fields_for(device_type, items):
    if device_type == "eMMC" and _runtime_emmc_active(items):
        return {"device_life_time_est_typ_a", "device_life_time_est_typ_b", "pre_eol_info", "bkops_status", "vendor_proprietary_health_report", "vendor_health_monitoring", "bad_block_count", "erase_cycle_count", "error_reporting"}
    return set(CONCLUSION_DIAGNOSTIC_FIELDS.get(device_type, set()))


def _key_order_for(device_type, items):
    if device_type == "eMMC" and _runtime_emmc_active(items):
        return [
            "native_nand_type", "pe_cycle", "data_retention", "endurance_condition",
            "partition_storage_mode", "device_life_time_est_typ_a",
            "device_life_time_est_typ_b", "pre_eol_info", "bkops_status",
            "vendor_health_monitoring", "reliable_write", "bkops",
        ]
    return CONCLUSION_KEY_ORDER.get(device_type, [])

FIELD_PRIORITY = {
    "pe_cycles":"P0","retention":"P0","tbw":"P0","dwpd":"P0","life_time_a":"P0","life_time_b":"P0","pre_eol":"P0",
    "percentage_used":"P0","data_units_written":"P0","smart_health":"P0","ext_csd_health_report":"P0",
    "cell_type":"P1","default_user_area_type":"P1","enhanced_area_cell_type":"P1","enhanced_user_data_area":"P1",
    "ecc_capability":"P1","ecc_status":"P1","internal_ecc":"P1","factory_bad_block":"P1","runtime_bad_block":"P1",
    "minimum_valid_blocks":"P1","bad_block_mark":"P1","bad_block_mark_location":"P1","program_fail":"P1","erase_fail":"P1",
    "status_register":"P1","read_retry":"P1","available_spare":"P1","spare_threshold":"P1","media_errors":"P1",
    "critical_warning":"P1","wear_level_remaining":"P1","endurance_class":"P1","over_provisioning":"P1","plp":"P1",
    # eMMC Runtime 37-field contract
    "pe_cycle":"P0","data_retention":"P0","device_life_time_est_typ_a":"P0","device_life_time_est_typ_b":"P0","pre_eol_info":"P0",
    "native_nand_type":"P1","partition_storage_mode":"P1","enhanced_user_data_area":"P1","default_user_data_area":"P1",
    "endurance_condition":"P1","bkops_status":"P1","vendor_proprietary_health_report":"P1","vendor_health_monitoring":"P1",
    "access_method":"P1","bad_block_count":"P1","erase_cycle_count":"P1","erase_cycle_granularity":"P1",
    "reliable_write":"P1","bkops":"P1","cache":"P1","sanitize":"P1","power_off_notification":"P1","error_reporting":"P1","field_firmware_update":"P1",
}

LEGACY_CANONICAL_ALIASES = {
    "nand_type":"cell_type",
    "ecc_requirement":"ecc_capability",
    "health_report":"ext_csd_health_report",
}


def _reviewed_candidate_relevant(canonical, item):
    """Hide mechanism prose from the compact reviewed view; raw candidates remain untouched below."""
    value = str(item.get("final_value") if item.get("final_value") is not None else item.get("ai_value") or "").strip()
    cond = str(item.get("condition") or "").strip()
    text = f"{value} {cond}".lower()
    words = re.findall(r"[A-Za-z]+", value)
    if canonical in {"ecc_requirement", "ecc_capability", "internal_ecc", "ecc_status"}:
        # Mechanism prose belongs to raw evidence even when its condition mentions ECC enabled.
        if any(token in text for token in ("calculates an ecc", "calculated and compared", "are ignored", "is protected", "not protected")):
            return False
        if re.search(r"\b\d+\s*(?:bits?|bytes?)\b|\b(?:enabled|disabled|default|optional)\b|ecc capability", text):
            return True
        return len(words) <= 7
    if canonical in {"bad_block_requirement", "factory_bad_block", "runtime_bad_block", "minimum_valid_blocks", "bad_block_mark", "bad_block_mark_location"}:
        if re.search(r"\b\d+\s*blocks?\b|\b00h\b|\bbyte\s*\d+\b|bad[- ]block mark|valid blocks", text):
            return True
        return len(words) <= 7
    return True


def _review_norm(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _review_equivalence_value(canonical: str, value: str, unit: str):
    raw = f"{value} {unit}".strip()
    if canonical == "capacity":
        compact = re.sub(r"[\s_-]+", "", raw)
        m = re.search(r"(?i)(\d+(?:\.\d+)?)(gbit|gbits|gb|mbit|mbits|mb|tbit|tbits|tb)$", compact)
        if m:
            number = m.group(1)
            original = re.search(r"(?i)(Gbit|Gbits|Gb|GB|Mbit|Mbits|Mb|MB|Tbit|Tbits|Tb|TB)\b", raw)
            token = original.group(1) if original else m.group(2)
            kind = token.lower() + "yte" if token in {"GB", "MB", "TB"} else token[0].lower() + "bit"
            return canonical, number, kind
    if canonical == "pe_cycles":
        compact = re.sub(r"[\s,]+", "", raw).casefold()
        m = re.match(r"(\d+(?:\.\d+)?)(k|m)?(?:cycles?)?$", compact)
        if m:
            mult = {None: 1, "k": 1000, "m": 1000000}[m.group(2)]
            return canonical, str(float(m.group(1)) * mult), "cycles"
    return canonical, _compact_match(value).casefold(), _compact_match(unit).casefold()


def _reviewed_target_canonical(candidate):
    canonical = candidate.get("canonical_name") or ""
    if canonical in LEGACY_CANONICAL_ALIASES:
        target = LEGACY_CANONICAL_ALIASES[canonical]
        if canonical == "ecc_requirement":
            value = str(candidate.get("final_value") if candidate.get("final_value") is not None else candidate.get("ai_value") or "")
            marker = f"{value} {candidate.get('condition') or ''}".lower()
            if re.search(r"\b\d+\s*(?:bits?|bytes?)\b", marker):
                return "ecc_capability"
            if any(x in marker for x in ("enabled", "disabled", "default", "internal ecc")):
                return "internal_ecc"
            return ""
        return target
    if canonical == "bad_block_requirement":
        value = str(candidate.get("final_value") if candidate.get("final_value") is not None else candidate.get("ai_value") or "")
        marker = f"{value} {candidate.get('condition') or ''} {candidate.get('source_text') or ''}".lower()
        if "valid block" in marker or re.search(r"\b\d+\s*blocks?\b", marker):
            return "minimum_valid_blocks"
        if re.search(r"\b(?:00h|[0-9a-f]{2}h)\b", marker) and "mark" in marker:
            return "bad_block_mark"
        if re.search(r"\bbyte\s*\d+\b", marker) or "spare area" in marker:
            return "bad_block_mark_location"
        return ""
    return canonical


def _reviewed_category(device_type, canonical):
    groups = _runtime_emmc_groups() if device_type == "eMMC" and canonical in set(_runtime_emmc_contract_fields()) else REVIEWED_FIELD_GROUPS.get(device_type, [])
    for group_name, fields in groups:
        if canonical in fields:
            return group_name
    return ""


def rebuild_reviewed_specifications(device_id):
    """Materialize a compact Reviewed Specification layer from current candidate state.

    Raw candidates and evidence remain untouched. This layer is rebuildable after AI Final Review
    or human confirmation and therefore never becomes an untraceable second source of truth.
    """
    import json
    from . import templates
    with connect() as con:
        device = con.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
    if not device:
        raise KeyError(device_id)
    dtype = templates.normalize_device_type(device["device_type"])
    allowed = set(templates.analysis_fields_for(dtype))
    raw = [c for c in list_candidates(device_id) if c.get("verify_status") != "rejected"]
    if dtype == "eMMC" and any(c.get("canonical_name") in set(_runtime_emmc_contract_fields()) for c in raw):
        allowed.update(_runtime_emmc_contract_fields())
    groups = {}
    for c in raw:
        canonical = _reviewed_target_canonical(c)
        if not canonical or canonical not in allowed:
            continue
        shadow = dict(c); shadow["canonical_name"] = canonical
        if not _reviewed_candidate_relevant(canonical, shadow):
            continue
        value = c.get("final_value") if c.get("final_value") is not None else c.get("ai_value")
        unit = c.get("final_unit") if c.get("final_unit") is not None else c.get("ai_unit")
        if value is None or not str(value).strip():
            continue
        eq = _review_equivalence_value(canonical, str(value), str(unit or ""))
        key = (canonical, eq, _review_norm(c.get("condition") or ""), _review_norm(c.get("scope") or ""))
        groups.setdefault(key, []).append((c, str(value), str(unit or "")))
    now_value = now()
    with connect() as con:
        con.execute("DELETE FROM reviewed_specifications WHERE device_id=?", (device_id,))
        for (canonical, _, _, _), bucket in groups.items():
            bucket.sort(key=lambda x: (0 if x[0].get("verify_status") == "confirmed" else 1, -float(x[0].get("confidence") or 0), x[0].get("source_page") or 9999))
            primary, value, unit = bucket[0]
            candidate_ids, evidence, seen = [], [], set()
            statuses = []
            for c, _, _ in bucket:
                if c.get("id"):
                    candidate_ids.append(c["id"])
                statuses.append(c.get("verify_status") or "pending")
                evs = c.get("evidence") or [{"source_page": c.get("source_page"), "source_section": c.get("source_section") or "", "source_text": c.get("source_text") or "", "confidence": c.get("confidence") or 0, "extraction_method": c.get("extraction_method") or ""}]
                for ev in evs:
                    ek = (ev.get("source_id") or "", ev.get("source_page"), ev.get("source_section") or "", ev.get("source_text") or "")
                    if ek in seen:
                        continue
                    seen.add(ek); evidence.append(ev)
            review_status = "confirmed" if any(x == "confirmed" for x in statuses) else "pending"
            con.execute("""INSERT INTO reviewed_specifications
              (id,device_id,canonical_name,parameter_name,category,priority,value,unit,condition,scope,candidate_ids_json,evidence_json,review_status,created_at,updated_at)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (uuid4().hex, device_id, canonical, templates.parameter_label(dtype, canonical, canonical),
               _reviewed_category(dtype, canonical), FIELD_PRIORITY.get(canonical, "P2"), value, unit,
               primary.get("condition") or "", primary.get("scope") or "", json.dumps(candidate_ids, ensure_ascii=False),
               json.dumps(evidence, ensure_ascii=False), review_status, now_value, now_value))
    items = list_reviewed_specifications(device_id)
    rebuild_device_conclusion(device_id, specs=items)
    return items


def list_reviewed_specifications(device_id):
    import json
    from . import templates
    with connect() as con:
        device = con.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        if not device:
            raise KeyError(device_id)
        data = rows(con, """SELECT * FROM reviewed_specifications WHERE device_id=?
          ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 ELSE 2 END, category, canonical_name, id""", (device_id,))
    out = []
    for item in data:
        item["candidate_ids"] = json.loads(item.pop("candidate_ids_json") or "[]")
        item["evidence"] = json.loads(item.pop("evidence_json") or "[]")
        item["evidence_count"] = len(item["evidence"])
        item["display"] = templates.format_spec_value(item["canonical_name"], item["value"], item["unit"])
        item["display_condition"] = templates.display_condition(item.get("condition"))
        item["display_scope"] = templates.display_scope(item.get("scope"))
        item["display_status"] = templates.display_status(item.get("review_status"))
        out.append(item)
    return out


def specification_workflow_status(device_id, specs=None):
    """Return the D2 confirmation/gate state without changing RC3 persistence contracts.

    Reviewed Specification may contain pending semantic facts so users can inspect a compact
    draft immediately after extraction.  A Device Conclusion becomes *formal* only when all
    P0/P1 reviewed facts are human-confirmed, required critical fields are present, and an
    extraction Review Gate (if any) is no longer unresolved.
    """
    from . import templates
    with connect() as con:
        device = con.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
    if not device:
        raise KeyError(device_id)
    items = list(specs) if specs is not None else list_reviewed_specifications(device_id)
    dtype = templates.normalize_device_type(device["device_type"])
    confirmed = [x for x in items if x.get("review_status") == "confirmed"]
    pending = [x for x in items if x.get("review_status") != "confirmed"]
    critical = _critical_fields_for(dtype, items)
    present_fields = {x.get("canonical_name") for x in items}
    confirmed_fields = {x.get("canonical_name") for x in confirmed}
    missing_critical = [f for f in critical if f not in present_fields]
    pending_critical = [f for f in critical if f in present_fields and f not in confirmed_fields]
    pending_key = []
    for x in pending:
        if x.get("priority") not in {"P0", "P1"}:
            continue
        field = x.get("canonical_name") or ""
        if field and field not in pending_key:
            pending_key.append(field)

    extraction = get_extraction_run(device_id)
    final_review = get_final_review(device_id)
    gate_required = bool(extraction and extraction.get("review_required"))
    gate_resolved = not gate_required or bool(final_review and final_review.get("overall_status") == "ready_for_human_review")
    review_attention = gate_required and not gate_resolved
    formal_ready = bool(items) and not review_attention and not missing_critical and not pending_key

    if review_attention:
        status = "attention_required"
    elif formal_ready:
        status = "confirmed"
    elif confirmed:
        status = "partially_confirmed"
    elif items:
        status = "pending_confirmation"
    else:
        status = "no_specification"

    label = lambda f: templates.parameter_label(dtype, f, f)
    return {
        "status": status,
        "formal_ready": formal_ready,
        "reviewed_spec_count": len(items),
        "confirmed_spec_count": len(confirmed),
        "pending_spec_count": len(pending),
        "missing_critical_fields": [label(f) for f in missing_critical],
        "pending_critical_fields": [label(f) for f in pending_critical],
        "pending_key_fields": [label(f) for f in pending_key],
        "review_gate_required": gate_required,
        "review_gate_resolved": gate_resolved,
        "final_review_status": (final_review or {}).get("overall_status", "not_run"),
    }


def reviewed_specs(device_id):
    """Return the persisted RC2 Reviewed Specification layer grouped for presentation."""
    from . import templates
    with connect() as con:
        device = con.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        count = con.execute("SELECT COUNT(*) FROM reviewed_specifications WHERE device_id=?", (device_id,)).fetchone()[0] if device else 0
    if not device:
        raise KeyError(device_id)
    if not count:
        rebuild_reviewed_specifications(device_id)
    items = list_reviewed_specifications(device_id)
    groups_out = []
    dtype = templates.normalize_device_type(device["device_type"])
    for group_name, fields in _reviewed_groups_for(dtype, items):
        specs = []
        for canonical in fields:
            vals = [x for x in items if x["canonical_name"] == canonical]
            if not vals:
                continue
            specs.append({"canonical_name": canonical, "parameter_name": vals[0]["parameter_name"], "priority": vals[0]["priority"],
                          "items": [{"candidate_ids": x["candidate_ids"], "display": x["display"], "condition": x["display_condition"],
                                     "scope": x["display_scope"], "verify_status": x["review_status"], "display_status": x["display_status"],
                                     "evidence_count": x["evidence_count"], "evidence": x["evidence"],
                                     "review_adjusted": any(c.get("verify_status") == "pending" for c in [])}
                                    for x in vals]})
        if specs:
            groups_out.append({"name": group_name, "specs": specs})
    raw = [c for c in list_candidates(device_id) if c.get("verify_status") != "rejected"]
    represented = {cid for x in items for cid in x.get("candidate_ids", [])}
    hidden_detail_count = sum(1 for c in raw if c.get("id") not in represented)
    review = get_final_review(device_id)
    return {
        "device": {"id": device["id"], "vendor": device["vendor"], "product_family": device["model"], "device_type": templates.normalize_device_type(device["device_type"])},
        "groups": groups_out,
        "review_status": (review or {}).get("overall_status", "not_run"),
        "review_summary": (review or {}).get("summary", "尚未执行最终整体审核"),
        "workflow": specification_workflow_status(device_id, specs=items),
        "hidden_detail_count": hidden_detail_count,
        "visible_field_count": sum(len(g["specs"]) for g in groups_out),
        "p0_count": sum(1 for x in items if x.get("priority") == "P0"),
        "p1_count": sum(1 for x in items if x.get("priority") == "P1"),
    }



CONCLUSION_GENERATOR_VERSION = "rc3-rules-v1"

CONCLUSION_KEY_ORDER = {
    "NOR Flash": ["pe_cycles", "retention", "lifetime_counter", "status_register", "program_fail", "erase_fail", "ecc_status", "operating_temperature"],
    "NAND Flash": ["pe_cycles", "retention", "cell_type", "ecc_capability", "ecc_status", "minimum_valid_blocks", "program_fail", "erase_fail", "status_register", "read_retry"],
    "eMMC": ["pe_cycles", "default_user_area_type", "enhanced_area_cell_type", "life_time_a", "life_time_b", "pre_eol", "ext_csd_health_report", "write_reliability", "bkops"],
    "SSD": ["tbw", "dwpd", "endurance_class", "cell_type", "smart_health", "percentage_used", "data_units_written", "available_spare", "media_errors", "critical_warning"],
}

CONCLUSION_CRITICAL_FIELDS = {
    "NOR Flash": ["pe_cycles", "retention"],
    "NAND Flash": ["pe_cycles", "retention", "ecc_capability"],
    "eMMC": ["life_time_a", "life_time_b", "pre_eol"],
    "SSD": ["tbw", "smart_health"],
}

CONCLUSION_LIFETIME_FIELDS = {
    "NOR Flash": {"pe_cycles", "retention", "lifetime_counter"},
    "NAND Flash": {"pe_cycles", "retention", "cell_type"},
    "eMMC": {"pe_cycles", "default_user_area_type", "enhanced_area_cell_type", "enhanced_user_data_area"},
    "SSD": {"tbw", "dwpd", "endurance_class", "cell_type", "warranty_write_limit"},
}

CONCLUSION_DIAGNOSTIC_FIELDS = {
    "NOR Flash": {"lifetime_counter", "status_register", "program_fail", "erase_fail", "error_flag", "ecc_status"},
    "NAND Flash": {"ecc_capability", "ecc_status", "minimum_valid_blocks", "factory_bad_block", "runtime_bad_block", "program_fail", "erase_fail", "status_register", "read_retry"},
    "eMMC": {"ext_csd_health_report", "life_time_a", "life_time_b", "pre_eol", "bkops", "error_reporting", "vendor_health_report"},
    "SSD": {"smart_health", "percentage_used", "data_units_written", "available_spare", "spare_threshold", "media_errors", "critical_warning", "temperature", "unsafe_shutdowns", "error_log", "wear_level_remaining"},
}


def _spec_values(by_name, canonical):
    vals = by_name.get(canonical) or []
    out = []
    for x in vals:
        text = x.get("display") or ""
        cond = x.get("display_condition") or ""
        if cond:
            text = f"{cond}：{text}"
        if text and text not in out:
            out.append(text)
    return out


def _compact_spec_text(by_name, canonical, label=""):
    vals = _spec_values(by_name, canonical)
    if not vals:
        return ""
    joined = " / ".join(vals[:4])
    return f"{label}{joined}" if label else joined


def _key_specs_for_conclusion(device_type, items, limit=8):
    order = _key_order_for(device_type, items)
    by_name = {}
    for x in items:
        by_name.setdefault(x["canonical_name"], []).append(x)
    out = []
    for canonical in order:
        vals = by_name.get(canonical) or []
        if not vals or vals[0].get("priority") not in {"P0", "P1"}:
            continue
        evidence = []
        seen = set()
        for x in vals:
            for ev in x.get("evidence") or []:
                key = (ev.get("source_id") or "", ev.get("source_page"), ev.get("source_section") or "", ev.get("source_text") or "")
                if key in seen:
                    continue
                seen.add(key); evidence.append(ev)
        out.append({
            "canonical_name": canonical,
            "parameter_name": vals[0]["parameter_name"],
            "priority": vals[0]["priority"],
            "values": [{"display": x["display"], "condition": x.get("display_condition") or "", "scope": x.get("display_scope") or "", "review_status": x.get("review_status") or "pending"} for x in vals],
            "evidence_count": len(evidence),
            "evidence": evidence[:12],
            "spec_ids": [x["id"] for x in vals],
        })
        if len(out) >= limit:
            break
    return out


def _build_device_conclusion(device_type, items, review=None):
    from . import templates
    by_name = {}
    for x in items:
        by_name.setdefault(x["canonical_name"], []).append(x)
    present = set(by_name)
    dtype = templates.normalize_device_type(device_type)

    if dtype == "NAND Flash":
        parts = []
        if _compact_spec_text(by_name, "cell_type"):
            parts.append("单元类型为" + _compact_spec_text(by_name, "cell_type"))
        if _compact_spec_text(by_name, "pe_cycles"):
            parts.append("标称擦写耐久度为" + _compact_spec_text(by_name, "pe_cycles"))
        if _compact_spec_text(by_name, "retention"):
            parts.append("数据保持为" + _compact_spec_text(by_name, "retention"))
        lifetime = "；".join(parts) + "。" if parts else "当前审核后规格尚未形成可直接量化写入寿命的关键参数。"
        diag_names = []
        for field, name in (("ecc_capability","ECC纠错"),("ecc_status","ECC状态"),("minimum_valid_blocks","有效块/坏块管理"),("program_fail","编程失败"),("erase_fail","擦除失败"),("status_register","状态寄存器"),("read_retry","读重试")):
            if field in present: diag_names.append(name)
        diagnostic = ("当前规格已识别" + "、".join(diag_names) + "等诊断能力。" if diag_names else "当前审核后规格尚未形成明确的介质健康诊断能力结果。")
        if "lifetime_counter" not in present:
            diagnostic += " 当前未识别到类似 SMART/EXT_CSD 的直接寿命计数结果。"
        software = "建议软件侧监控 ECC/坏块及 Program/Erase Fail 等状态；若器件不提供直接寿命计数，需结合高频写入区域的擦写策略或写入统计管理寿命。"
    elif dtype == "NOR Flash":
        parts = []
        if _compact_spec_text(by_name, "pe_cycles"): parts.append("标称擦写耐久度为" + _compact_spec_text(by_name, "pe_cycles"))
        if _compact_spec_text(by_name, "retention"): parts.append("数据保持为" + _compact_spec_text(by_name, "retention"))
        lifetime = "；".join(parts) + "。" if parts else "当前审核后规格尚未形成明确的 P/E Cycle 或数据保持结论。"
        diag_names=[]
        for field,name in (("lifetime_counter","寿命计数"),("status_register","状态寄存器"),("program_fail","编程失败"),("erase_fail","擦除失败"),("error_flag","错误标志"),("ecc_status","ECC状态")):
            if field in present: diag_names.append(name)
        diagnostic=("当前规格已识别"+"、".join(diag_names)+"等诊断能力。" if diag_names else "当前审核后规格尚未形成明确的运行诊断能力结果。")
        software="建议软件侧至少监控 Program/Erase 失败状态；当前若未识别寿命计数能力，应对高频擦写区域自行维护擦写次数或更新策略。"
    elif dtype == "eMMC":
        runtime_contract = _runtime_emmc_active(items)
        parts=[]
        if runtime_contract:
            if _compact_spec_text(by_name,"native_nand_type"): parts.append("原生 NAND 类型为"+_compact_spec_text(by_name,"native_nand_type"))
            if _compact_spec_text(by_name,"pe_cycle"): parts.append("标称擦写耐久度为"+_compact_spec_text(by_name,"pe_cycle"))
            if _compact_spec_text(by_name,"data_retention"): parts.append("数据保持为"+_compact_spec_text(by_name,"data_retention"))
            if _compact_spec_text(by_name,"partition_storage_mode"): parts.append("分区存储模式为"+_compact_spec_text(by_name,"partition_storage_mode"))
            lifetime="；".join(parts)+"。" if parts else "当前审核后规格未形成明确的介质类型、P/E Cycle 或数据保持结论。"
            diag_names=[]
            for field,name in (("device_life_time_est_typ_a","寿命估算A"),("device_life_time_est_typ_b","寿命估算B"),("pre_eol_info","Pre-EOL预警"),("bkops_status","BKOPS_STATUS"),("vendor_proprietary_health_report","厂商专有健康报告"),("vendor_health_monitoring","厂商增强健康监测"),("error_reporting","错误报告")):
                if field in present: diag_names.append(name)
            diagnostic=("当前规格已识别"+"、".join(diag_names)+"，可用于软件侧读取介质健康状态。" if diag_names else "当前审核后规格尚未形成寿命/健康诊断结果。")
        else:
            if _compact_spec_text(by_name,"default_user_area_type"): parts.append("默认用户区为"+_compact_spec_text(by_name,"default_user_area_type"))
            if _compact_spec_text(by_name,"enhanced_area_cell_type"): parts.append("增强区为"+_compact_spec_text(by_name,"enhanced_area_cell_type"))
            if _compact_spec_text(by_name,"pe_cycles"): parts.append("标称擦写耐久度为"+_compact_spec_text(by_name,"pe_cycles"))
            lifetime="；".join(parts)+"。" if parts else "当前审核后规格未形成明确的用户区介质类型或 P/E Cycle 结论。"
            diag_names=[]
            for field,name in (("ext_csd_health_report","EXT_CSD健康报告"),("life_time_a","寿命估算A"),("life_time_b","寿命估算B"),("pre_eol","Pre-EOL预警"),("bkops","BKOPS"),("error_reporting","错误报告")):
                if field in present: diag_names.append(name)
            diagnostic=("当前规格已识别"+"、".join(diag_names)+"，可用于软件侧读取介质健康状态。" if diag_names else "当前审核后规格尚未形成 EXT_CSD 寿命/健康诊断结果。")
        software="建议周期读取已识别的寿命估算、Pre-EOL、BKOPS 与厂商健康字段并建立告警；对未明确的 P/E Cycle 或数据保持参数不得推断，应保留缺口并进入人工确认。"
    elif dtype == "SSD":
        parts=[]
        if _compact_spec_text(by_name,"cell_type"): parts.append("NAND单元类型为"+_compact_spec_text(by_name,"cell_type"))
        if _compact_spec_text(by_name,"tbw"): parts.append("标称耐久度为"+_compact_spec_text(by_name,"tbw"))
        if _compact_spec_text(by_name,"dwpd"): parts.append("工作负载耐久度为"+_compact_spec_text(by_name,"dwpd"))
        if _compact_spec_text(by_name,"endurance_class"): parts.append("耐久等级为"+_compact_spec_text(by_name,"endurance_class"))
        lifetime="；".join(parts)+"。" if parts else "当前审核后规格尚未形成 TBW/DWPD 等可量化耐久度结果。"
        diag_names=[]
        for field,name in (("smart_health","SMART/NVMe Health"),("percentage_used","已用寿命比例"),("data_units_written","累计写入量"),("available_spare","可用备用空间"),("media_errors","介质错误"),("critical_warning","关键告警"),("temperature","温度"),("error_log","错误日志")):
            if field in present: diag_names.append(name)
        diagnostic=("当前规格已识别"+"、".join(diag_names)+"等健康诊断能力。" if diag_names else "当前审核后规格尚未形成 SMART/NVMe Health 诊断结果。")
        software="建议周期采集 SMART/NVMe Health 中的寿命消耗、累计写入量、备用空间与介质错误等字段，并结合产品规格设置预警。"
    else:
        lifetime="当前器件类型尚未配置结果化寿命结论模板。"
        diagnostic="当前器件类型尚未配置结果化诊断结论模板。"
        software="请人工核对 Reviewed Specification 后补充软件监控策略。"

    critical = _critical_fields_for(dtype, items)
    labels = {}
    if dtype == "eMMC" and _runtime_emmc_active(items):
        try:
            from .runtime_domain_strategy import EMMC_FIELD_LABELS
            labels = EMMC_FIELD_LABELS
        except Exception:
            labels = {}
    missing = [labels.get(f) or templates.parameter_label(dtype, f, f) for f in critical if f not in present]
    lifetime_found = bool(present & _lifetime_fields_for(dtype, items))
    diagnostic_found = bool(present & _diagnostic_fields_for(dtype, items))
    if lifetime_found and diagnostic_found and not missing:
        status = "complete"
    elif lifetime_found or diagnostic_found:
        status = "partial"
    else:
        status = "insufficient"
    risks=[]
    if missing:
        risks.append("关键规格信息缺口：" + "、".join(missing))
    if review and review.get("overall_status") == "attention_required":
        risks.append("Final Review 仍有需要重点人工检查的字段或证据。")
    key_specs = _key_specs_for_conclusion(dtype, items, 8)
    for spec in key_specs:
        spec["parameter_knowledge"] = templates.parameter_knowledge(dtype, spec.get("canonical_name"))
    return {
        "lifetime_summary": lifetime,
        "diagnostic_summary": diagnostic,
        "software_recommendation": software,
        "risks": risks,
        "missing_critical_fields": missing,
        "key_specs": key_specs,
        "conclusion_status": status,
        "generator_version": CONCLUSION_GENERATOR_VERSION,
    }


def rebuild_device_conclusion(device_id, specs=None):
    import json
    from . import templates
    with connect() as con:
        device = con.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
    if not device:
        raise KeyError(device_id)
    items = specs if specs is not None else list_reviewed_specifications(device_id)
    review = get_final_review(device_id)
    result = _build_device_conclusion(templates.normalize_device_type(device["device_type"]), items, review)
    generated = now()
    with connect() as con:
        con.execute("""INSERT INTO device_conclusions
          (device_id,lifetime_summary,diagnostic_summary,software_recommendation,risks_json,missing_critical_fields_json,key_specs_json,conclusion_status,generator_version,generated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(device_id) DO UPDATE SET lifetime_summary=excluded.lifetime_summary,
          diagnostic_summary=excluded.diagnostic_summary,software_recommendation=excluded.software_recommendation,
          risks_json=excluded.risks_json,missing_critical_fields_json=excluded.missing_critical_fields_json,
          key_specs_json=excluded.key_specs_json,conclusion_status=excluded.conclusion_status,
          generator_version=excluded.generator_version,generated_at=excluded.generated_at""",
          (device_id,result["lifetime_summary"],result["diagnostic_summary"],result["software_recommendation"],
           json.dumps(result["risks"], ensure_ascii=False), json.dumps(result["missing_critical_fields"], ensure_ascii=False),
           json.dumps(result["key_specs"], ensure_ascii=False), result["conclusion_status"], result["generator_version"], generated))
    return get_device_conclusion(device_id)


def get_device_conclusion(device_id, rebuild_if_missing=True):
    import json
    with connect() as con:
        device = con.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        row = con.execute("SELECT * FROM device_conclusions WHERE device_id=?", (device_id,)).fetchone() if device else None
    if not device:
        raise KeyError(device_id)
    if not row and rebuild_if_missing:
        return rebuild_device_conclusion(device_id)
    if not row:
        return None
    item=dict(row)
    item["risks"] = json.loads(item.pop("risks_json") or "[]")
    item["missing_critical_fields"] = json.loads(item.pop("missing_critical_fields_json") or "[]")
    item["key_specs"] = json.loads(item.pop("key_specs_json") or "[]")
    item["device"] = {"id": device["id"], "vendor": device["vendor"], "product_family": device["model"], "device_type": device["device_type"]}
    workflow = specification_workflow_status(device_id)
    item["verification_status"] = workflow["status"]
    item["is_formal"] = workflow["formal_ready"]
    item["confirmation"] = workflow
    return item


def family_view(device_id):
    """Build a compact product-family presentation.

    Common specs are shown once. The part-number matrix contains only fields that have
    model-specific values (or unbound variant candidates), so family-wide facts are never
    repeated across every part-number column.
    """
    from . import templates
    with connect() as con:
        device = con.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
    if not device:
        raise KeyError(device_id)
    models = [m for m in list_models(device_id) if m.get("verify_status") != "rejected"]
    candidates = [c for c in list_candidates(device_id) if c.get("verify_status") != "rejected"]
    model_items = []
    for m in models:
        model_items.append({
            "id": m["id"], "model": m.get("final_model") or m.get("ai_model") or "",
            "scope": m.get("scope") or "", "verify_status": m.get("verify_status") or "pending"
        })
    common, variants, unbound = [], [], []
    field_labels = templates.fields_for(device["device_type"])
    for c in candidates:
        value = c["final_value"] if c["final_value"] is not None else c["ai_value"]
        unit = c["final_unit"] if c["final_unit"] is not None else c["ai_unit"]
        item = {
            "id": c["id"], "canonical_name": c["canonical_name"],
            "parameter_name": field_labels.get(c["canonical_name"], c["parameter_name"]),
            "value": value, "unit": unit,
            "display_value": templates.format_spec_value(c["canonical_name"], value, unit),
            "condition": c.get("condition") or "", "display_condition": templates.display_condition(c.get("condition")),
            "scope": c.get("scope") or "", "display_scope": templates.display_scope(c.get("scope")),
            "verify_status": c.get("verify_status") or "pending", "display_status": templates.display_status(c.get("verify_status")),
            "source_page": c.get("source_page"),
            "source_section": c.get("source_section") or "", "source_text": c.get("source_text") or "",
            "evidence": c.get("evidence") or [], "review_adjusted": c["final_value"] is not None and c.get("verify_status") == "pending"
        }
        if _is_common_scope(item["scope"], device["model"]):
            item["model_ids"] = []
            common.append(item)
            continue
        matched = [m["id"] for m in model_items if _model_matches_scope(item["scope"], m)]
        item["model_ids"] = matched
        if matched:
            variants.append(item)
        else:
            unbound.append(item)

    all_fields = []
    seen_fields = set()
    for c in candidates:
        key = c["canonical_name"]
        if key not in seen_fields:
            seen_fields.add(key)
            all_fields.append((key, field_labels.get(key, c["parameter_name"])))
    # Equivalent raw candidates (for example 1Gb / 1Gbit repeated on several pages)
    # become one visible common specification with multiple evidence records.
    common = _collapse_family_specs(common)
    rows_out = []
    common_by_field = {}
    for c in common:
        common_by_field.setdefault(c["canonical_name"], []).append(c)
    variant_by_model_field = {}
    for c in variants:
        for mid in c["model_ids"]:
            variant_by_model_field.setdefault((mid, c["canonical_name"]), []).append(c)
    unbound_fields = {c["canonical_name"] for c in unbound}
    for canonical, label in all_fields:
        cells = {}
        direct_any = False
        values = []
        has_common = bool(common_by_field.get(canonical))
        for m in model_items:
            direct = _collapse_family_specs(variant_by_model_field.get((m["id"], canonical), []))
            direct_any = direct_any or bool(direct)
            cell = []
            if direct:
                for c in direct:
                    display = c.get("display_value") or templates.format_spec_value(canonical, c["value"], c["unit"])
                    if c.get("display_condition"):
                        display += f" · {c['display_condition']}"
                    cell.append({"candidate_id": c["id"], "candidate_ids": c.get("candidate_ids") or [c["id"]],
                                 "display": display, "value": c["value"], "unit": c["unit"],
                                 "condition": c["condition"], "scope": c["scope"], "inherited": False,
                                 "reference_common": False, "verify_status": c["verify_status"]})
                    values.append(display)
            elif has_common:
                # Do not duplicate the public value into every PN column. One compact marker is enough.
                cell.append({"candidate_id": None, "candidate_ids": [], "display": "同公共规格",
                             "value": "", "unit": "", "condition": "", "scope": "",
                             "inherited": True, "reference_common": True, "verify_status": ""})
            cells[m["id"]] = cell
        # Purely common fields belong only in the common-spec tab, not in the matrix.
        if not direct_any and canonical not in unbound_fields:
            continue
        normalized_values = {re.sub(r"\s+", " ", x.strip().lower()) for x in values if x.strip()}
        difference = len(normalized_values) > 1 or direct_any or canonical in unbound_fields
        rows_out.append({"canonical_name": canonical, "parameter_name": label, "cells": cells,
                         "is_difference": difference, "has_unbound": canonical in unbound_fields,
                         "has_common": has_common})
    return {
        "device": {"id": device["id"], "vendor": device["vendor"], "product_family": device["model"],
                   "device_type": device["device_type"]},
        "models": model_items, "common_specs": common, "variant_specs": variants, "unbound_specs": unbound,
        "document_identity": get_document_identity(device_id),
        "document_analysis": (get_extraction_run(device_id) or {}).get("document_analysis") or {},
        "conclusion": get_device_conclusion(device_id),
        "workflow": specification_workflow_status(device_id),
        "matrix_rows": rows_out, "difference_rows": [r for r in rows_out if r["is_difference"]],
        "counts": {"models": len(model_items), "common_specs": len(common), "variant_specs": len(variants),
                   "unbound_specs": len(unbound), "difference_fields": sum(1 for r in rows_out if r["is_difference"])},
    }

def list_devices():
    with connect() as con:
        return rows(con, """SELECT d.*,s.filename,s.original_url,s.publisher,s.page_count,s.created_at,s.sha256,
        i.document_number,i.revision,i.revision_date,i.document_status,i.document_variant,i.language,i.identity_key,
        p.parser_version,p.table_count,
        (SELECT COUNT(*) FROM document_models m WHERE m.device_id=d.id) AS model_candidate_count,
        (SELECT COUNT(*) FROM candidates c WHERE c.device_id=d.id) AS candidate_count,
        (SELECT COUNT(*) FROM candidates c WHERE c.device_id=d.id AND c.verify_status='confirmed') AS confirmed_candidate_count,
        (SELECT COUNT(*) FROM reviewed_specifications r WHERE r.device_id=d.id) AS reviewed_spec_count,
        dc.conclusion_status AS conclusion_status
        FROM devices d JOIN sources s ON s.id=d.source_id
        LEFT JOIN document_identities i ON i.device_id=d.id
        LEFT JOIN parsed_documents p ON p.source_id=s.id
        LEFT JOIN device_conclusions dc ON dc.device_id=d.id
        ORDER BY s.created_at DESC,d.device_type,d.model""")


def delete_device(device_id):
    """Delete one imported datasheet record and all data owned by it.

    The source PDF row/file is deleted only when no other device still references the same source.
    This keeps deletion safe even if future flows allow one PDF source to back multiple product-family rows.
    """
    with connect() as con:
        row = con.execute("""SELECT d.id,d.source_id,s.local_path,s.filename,p.md_path FROM devices d
          JOIN sources s ON s.id=d.source_id LEFT JOIN parsed_documents p ON p.source_id=s.id WHERE d.id=?""", (device_id,)).fetchone()
        if not row:
            raise KeyError(device_id)
        source_id = row["source_id"]
        local_path = row["local_path"]
        md_path = row["md_path"]
        counts = {
            "candidate_count": con.execute("SELECT COUNT(*) FROM candidates WHERE device_id=?", (device_id,)).fetchone()[0],
            "model_candidate_count": con.execute("SELECT COUNT(*) FROM document_models WHERE device_id=?", (device_id,)).fetchone()[0],
            "link_count": con.execute("SELECT COUNT(*) FROM links WHERE device_id=?", (device_id,)).fetchone()[0],
            "review_count": con.execute("SELECT COUNT(*) FROM final_reviews WHERE device_id=?", (device_id,)).fetchone()[0],
        }
        # Delete dependents explicitly because older databases did not define ON DELETE CASCADE
        # consistently for every table. candidate_evidence follows candidates via FK cascade.
        con.execute("DELETE FROM candidate_evidence WHERE candidate_id IN (SELECT id FROM candidates WHERE device_id=?)", (device_id,))
        con.execute("DELETE FROM candidates WHERE device_id=?", (device_id,))
        con.execute("DELETE FROM document_models WHERE device_id=?", (device_id,))
        con.execute("DELETE FROM final_review_corrections WHERE device_id=?", (device_id,))
        con.execute("DELETE FROM final_reviews WHERE device_id=?", (device_id,))
        con.execute("DELETE FROM reviewed_specifications WHERE device_id=?", (device_id,))
        con.execute("DELETE FROM device_conclusions WHERE device_id=?", (device_id,))
        con.execute("DELETE FROM document_identities WHERE device_id=?", (device_id,))
        con.execute("DELETE FROM links WHERE device_id=?", (device_id,))
        con.execute("DELETE FROM devices WHERE id=?", (device_id,))
        remaining = con.execute("SELECT COUNT(*) FROM devices WHERE source_id=?", (source_id,)).fetchone()[0]
        source_deleted = remaining == 0
        if source_deleted:
            con.execute("DELETE FROM parsed_documents WHERE source_id=?", (source_id,))
            con.execute("DELETE FROM sources WHERE id=?", (source_id,))
    file_deleted = False
    if source_deleted and local_path:
        try:
            path = Path(local_path)
            if path.exists():
                path.unlink()
                file_deleted = True
        except OSError:
            # Database deletion is authoritative; stale local files can be cleaned manually.
            file_deleted = False
    md_deleted = False
    if source_deleted and md_path:
        try:
            mpath = Path(md_path)
            if mpath.exists():
                mpath.unlink(); md_deleted = True
            try:
                mpath.parent.rmdir()
            except OSError:
                pass
        except OSError:
            md_deleted = False
    return {
        "device_id": device_id, "source_id": source_id, "filename": row["filename"],
        "deleted": True, "source_deleted": source_deleted, "file_deleted": file_deleted, "markdown_deleted": md_deleted, **counts
    }



def _decorate_candidate_for_display(item, device_type):
    from . import templates
    item = dict(item)
    item["parameter_name"] = templates.parameter_label(device_type, item.get("canonical_name"), item.get("parameter_name") or "")
    item["parameter_knowledge"] = templates.parameter_knowledge(device_type, item.get("canonical_name"))
    item["display_ai_value"] = templates.format_spec_value(item.get("canonical_name"), item.get("ai_value"), item.get("ai_unit"))
    final_value = item.get("final_value")
    final_unit = item.get("final_unit")
    item["display_final_value"] = templates.format_spec_value(item.get("canonical_name"), final_value, final_unit) if final_value is not None else None
    item["display_condition"] = templates.display_condition(item.get("condition"))
    item["display_scope"] = templates.display_scope(item.get("scope"))
    item["display_status"] = templates.display_status(item.get("verify_status"))
    return item

def list_candidates(device_id):
    with connect() as con:
        device = con.execute("SELECT device_type FROM devices WHERE id=?", (device_id,)).fetchone()
        if not device:
            return []
        items = rows(con, """SELECT c.*,s.filename,s.original_url,s.publisher FROM candidates c
        JOIN devices d ON d.id=c.device_id JOIN sources s ON s.id=d.source_id
        WHERE c.device_id=? ORDER BY c.source_page,c.canonical_name""", (device_id,))
        out = []
        for item in items:
            item["evidence"] = rows(con, """SELECT p.source_id,e.source_page,e.source_section,e.source_text,e.confidence,e.extraction_method,e.scope
              FROM candidate_evidence e LEFT JOIN candidate_evidence_provenance p ON p.evidence_id=e.id
              WHERE e.candidate_id=? ORDER BY e.source_page,e.id""", (item["id"],))
            out.append(_decorate_candidate_for_display(item, device["device_type"]))
        return out


def verify(candidate_id, status, value, unit, by, condition=None, scope=None):
    """Human review of one extracted candidate.

    The RC3 candidate remains the review source of truth.  AI extraction is immutable in
    ``ai_value``/``ai_unit``; each human action is appended to ``candidate_review_history``
    so Edit + Confirm never destroys the original value or previous reviewed value.
    """
    import json
    if status not in {"confirmed", "rejected"} or not by.strip():
        raise ValueError("需选择确认或驳回，并填写核对人")
    reviewed_at = now()
    with connect() as con:
        candidate = con.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        if not candidate:
            raise KeyError(candidate_id)
        final_value = (value if value is not None else candidate["ai_value"]).strip()
        final_unit = str(unit if unit is not None else candidate["ai_unit"] or "").strip()
        final_condition = candidate["condition"] if condition is None else str(condition).strip()
        final_scope = candidate["scope"] if scope is None else str(scope).strip()
        if status == "confirmed" and not final_value:
            raise ValueError("确认值不能为空")
        if status == "confirmed":
            other = con.execute("""SELECT id FROM candidates WHERE device_id=? AND canonical_name=?
                AND condition=? AND scope=? AND verify_status='confirmed' AND id<>? LIMIT 1""",
                (candidate["device_id"], candidate["canonical_name"], final_condition, final_scope, candidate_id)).fetchone()
            if other:
                raise ConfirmationConflict("同一器件、字段、条件和适用范围已有已确认规格；请先驳回或更正原记录")

        old_value = candidate["final_value"] if candidate["final_value"] is not None else candidate["ai_value"]
        old_unit = candidate["final_unit"] if candidate["final_unit"] is not None else candidate["ai_unit"]
        changed = any([
            str(final_value or "") != str(candidate["ai_value"] or ""),
            str(final_unit or "") != str(candidate["ai_unit"] or ""),
            str(final_condition or "") != str(candidate["condition"] or ""),
            str(final_scope or "") != str(candidate["scope"] or ""),
        ])
        action = "reject" if status == "rejected" else ("edit_confirm" if changed else "confirm")
        version = int(con.execute("SELECT COUNT(*) FROM candidate_review_history WHERE candidate_id=?", (candidate_id,)).fetchone()[0]) + 1
        evidence = rows(con, """SELECT p.source_id,e.source_page,e.source_section,e.source_text
          FROM candidate_evidence e LEFT JOIN candidate_evidence_provenance p ON p.evidence_id=e.id
          WHERE e.candidate_id=? ORDER BY e.source_page,e.id""", (candidate_id,))
        if not evidence:
            evidence = [{"source_id": None, "source_page": candidate["source_page"],
                         "source_section": candidate["source_section"], "source_text": candidate["source_text"]}]
        con.execute("""INSERT INTO candidate_review_history
          (id,candidate_id,device_id,version,action,prior_status,new_status,ai_value,ai_unit,
           old_final_value,old_final_unit,old_condition,old_scope,new_final_value,new_final_unit,
           new_condition,new_scope,evidence_refs_json,reviewed_by,reviewed_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (uuid4().hex, candidate_id, candidate["device_id"], version, action, candidate["verify_status"], status,
           candidate["ai_value"], candidate["ai_unit"], old_value, old_unit, candidate["condition"], candidate["scope"],
           final_value, final_unit, final_condition, final_scope, json.dumps(evidence, ensure_ascii=False), by.strip(), reviewed_at))
        con.execute("""UPDATE candidates SET verify_status=?, final_value=?, final_unit=?,
        condition=?, scope=?, verified_by=?, verified_at=? WHERE id=?""",
        (status, final_value, final_unit, final_condition, final_scope, by.strip(), reviewed_at, candidate_id))
    rebuild_reviewed_specifications(candidate["device_id"])
    return {"id": candidate_id, "verify_status": status, "review_action": action, "review_version": version}


def list_candidate_review_history(candidate_id):
    import json
    with connect() as con:
        if not con.execute("SELECT 1 FROM candidates WHERE id=?", (candidate_id,)).fetchone():
            raise KeyError(candidate_id)
        items = rows(con, """SELECT * FROM candidate_review_history WHERE candidate_id=?
          ORDER BY version DESC, reviewed_at DESC""", (candidate_id,))
    for item in items:
        item["evidence_refs"] = json.loads(item.pop("evidence_refs_json") or "[]")
    return items



def delete_candidate(candidate_id):
    with connect() as con:
        row = con.execute("SELECT device_id FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        if not row:
            raise KeyError(candidate_id)
        device_id = row["device_id"]
        con.execute("DELETE FROM final_review_corrections WHERE candidate_id=?", (candidate_id,))
        con.execute("DELETE FROM candidates WHERE id=?", (candidate_id,))
        # Final Review is a snapshot of the candidate set; invalidate it after any deletion.
        con.execute("DELETE FROM final_reviews WHERE device_id=?", (device_id,))
    rebuild_reviewed_specifications(device_id)
    return {"id": candidate_id, "device_id": device_id, "deleted": True}


def clear_candidates(device_id):
    with connect() as con:
        if not con.execute("SELECT 1 FROM devices WHERE id=?", (device_id,)).fetchone():
            raise KeyError(device_id)
        count = con.execute("SELECT COUNT(*) FROM candidates WHERE device_id=?", (device_id,)).fetchone()[0]
        con.execute("DELETE FROM final_review_corrections WHERE device_id=?", (device_id,))
        con.execute("DELETE FROM candidates WHERE device_id=?", (device_id,))
        # Keep the source PDF, product-family device row and document_models; only parameter
        # candidates/evidence and the now-stale Final Review snapshot are cleared.
        con.execute("DELETE FROM final_reviews WHERE device_id=?", (device_id,))
        con.execute("DELETE FROM reviewed_specifications WHERE device_id=?", (device_id,))
        con.execute("DELETE FROM device_conclusions WHERE device_id=?", (device_id,))
    return {"device_id": device_id, "deleted_count": count}

def confirmed(device_ids):
    if not device_ids:
        return []
    marks = ",".join("?" for _ in device_ids)
    with connect() as con:
        return rows(con, f"""SELECT c.*,d.vendor,d.model,d.device_type,s.filename,s.original_url,s.publisher
        FROM candidates c JOIN devices d ON d.id=c.device_id JOIN sources s ON s.id=d.source_id
        WHERE c.verify_status='confirmed' AND c.device_id IN ({marks})""", device_ids)


def compare(device_ids):
    if len(set(device_ids)) < 2:
        raise ValueError("至少选择两个不同器件")
    devices = {d["id"]: d for d in list_devices() if d["id"] in device_ids}
    if len(devices) != len(set(device_ids)):
        raise ValueError("器件不存在")
    specs = confirmed(device_ids)
    matrix = {}
    for spec in specs:
        matrix.setdefault(spec["canonical_name"], {})[spec["device_id"]] = spec
    return {"devices": list(devices.values()), "fields": matrix,
            "missing": {field: [d for d in device_ids if d not in values] for field, values in matrix.items()}}


def query_knowledge(q):
    terms = [t.lower() for t in re.findall(r"[A-Za-z_0-9]+|[\u4e00-\u9fff]{2,}", q)]
    if not terms:
        return {"answer": "请输入查询词", "facts": [], "status": "no_evidence"}
    with connect() as con:
        data = rows(con, """SELECT c.*,d.vendor,d.model,d.device_type,s.filename,s.original_url,s.publisher
        FROM candidates c JOIN devices d ON d.id=c.device_id JOIN sources s ON s.id=d.source_id
        WHERE c.verify_status='confirmed'""")
    ranked = []
    for item in data:
        hay = " ".join(str(item[k] or "") for k in ("canonical_name", "parameter_name", "source_text", "model", "device_type")).lower()
        score = sum(1 for term in terms if term in hay)
        if score:
            ranked.append((score, item))
    facts = [x for _, x in sorted(ranked, key=lambda x: -x[0])[:10]]
    return {"answer": "；".join(f"{x['model']} {x['parameter_name']}: {x['final_value']} {x['final_unit']}".strip() for x in facts)
            if facts else "待验证：没有找到已确认且匹配的规格证据", "facts": facts,
            "status": "evidenced" if facts else "no_evidence"}


def impact_draft(old_id, new_id):
    comparison = compare([old_id, new_id])
    changes = []
    for field, values in comparison["fields"].items():
        a, b = values.get(old_id), values.get(new_id)
        if not a or not b or (a["final_value"], a["final_unit"]) == (b["final_value"], b["final_unit"]):
            continue
        changes.append({"field": field, "fact": f"{a['final_value']} {a['final_unit']} → {b['final_value']} {b['final_unit']}",
                        "evidence": [a, b], "analysis": "需评估相关软件参数、监测与测试覆盖；具体影响待工程评审。"})
    return {"status": "draft_for_review", "changes": changes,
            "unverified": "缺少相同业务负载、控制器实现和现场写入量证据；不得作为正式标准。"}
