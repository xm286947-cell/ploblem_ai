from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


MATERIAL_TYPES = {"ESCAPE_ANALYSIS", "ITR_SOURCE", "ITR_CS", "SOFTWARE_OPERATION", "BATCH_ISSUE", "TEST_ISSUE"}
DEFAULT_GROUPS = (
    ("DG-ESCAPE", "ESCAPE_ANALYSIS", "漏测分析", 1, 1, 1),
    ("DG-ITR", "ITR_SOURCE", "ITR原始问题", 0, 0, 0),
    ("DG-ITR-CS", "ITR_CS", "ITR彻底解决单", 0, 0, 0),
    ("DG-SW-OPS", "SOFTWARE_OPERATION", "软件问题运营数据", 0, 0, 0),
    ("DG-BATCH", "BATCH_ISSUE", "批量问题", 0, 0, 0),
    ("DG-TEST", "TEST_ISSUE", "测试问题", 0, 0, 0),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS data_group(
 group_id TEXT PRIMARY KEY,group_code TEXT NOT NULL UNIQUE,group_name TEXT NOT NULL,
 material_type TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,
 include_ai INTEGER NOT NULL DEFAULT 0,include_insight INTEGER NOT NULL DEFAULT 0,
 include_report INTEGER NOT NULL DEFAULT 0,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS source_material(
 material_id TEXT PRIMARY KEY,group_id TEXT NOT NULL REFERENCES data_group(group_id),
 material_type TEXT NOT NULL,business_key TEXT NOT NULL,canonical_itr TEXT,
 version_no INTEGER NOT NULL,source_hash TEXT NOT NULL,source_file TEXT,sheet_name TEXT,row_number INTEGER,
 raw_json TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(group_id,business_key,source_hash));
CREATE INDEX IF NOT EXISTS idx_source_material_itr ON source_material(canonical_itr,material_type);
CREATE TABLE IF NOT EXISTS association_rule(
 rule_id TEXT PRIMARY KEY,rule_name TEXT NOT NULL,source_type TEXT NOT NULL,target_type TEXT NOT NULL,
 source_field TEXT NOT NULL,target_field TEXT NOT NULL,transform TEXT NOT NULL DEFAULT 'EXACT',
 priority INTEGER NOT NULL DEFAULT 100,status TEXT NOT NULL DEFAULT 'ACTIVE',version_no INTEGER NOT NULL DEFAULT 1,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS issue_material_link(
 link_id TEXT PRIMARY KEY,knowledge_id TEXT NOT NULL,material_id TEXT NOT NULL REFERENCES source_material(material_id),
 rule_id TEXT REFERENCES association_rule(rule_id),link_status TEXT NOT NULL,match_value TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(knowledge_id,material_id));
CREATE TABLE IF NOT EXISTS source_material_reporting_year(
 material_id TEXT PRIMARY KEY REFERENCES source_material(material_id) ON DELETE CASCADE,
 reporting_year TEXT NOT NULL,year_source TEXT NOT NULL,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_source_material_reporting_year ON source_material_reporting_year(reporting_year,material_id);
"""


def _clean(value: Any) -> str:
    return str(value or "").strip()


def normalize_itr(value: Any) -> str:
    text = re.sub(r"\s+", "", _clean(value)).upper()
    return text[:-2] if text.endswith("CS") else text


def year_from_itr(value: Any) -> str:
    match = re.match(r"^ITR(20\d{2})", normalize_itr(value))
    return match.group(1) if match else ""


def _first(raw: dict[str, Any], *names: str) -> str:
    for name in names:
        value = _clean(raw.get(name))
        if value:
            return value
    return ""


def effective_reporting_year(raw: dict[str, Any], business_key: Any, reporting_year: Any = "", year_source: Any = "") -> str:
    """Software KPI year: manual override > KPI date > file year > ITR fallback."""
    stored=_clean(reporting_year);source=_clean(year_source)
    if stored and source in {"BATCH_MANUAL", "IMPORT_MANUAL"}:
        return stored
    kpi_month=_first(raw, "数据运营_KPI计入月份", "KPI计入月份")
    dated=re.search(r"(20\d{2})\s*(?:年|[-/.])", kpi_month)
    if dated:
        return dated.group(1)
    file_year=_first(raw, "数据运营_KPI计入年份", "数据运营_KPI计入年度", "KPI计入年份", "考核年份")
    return file_year or stored or year_from_itr(business_key)


def combine_headers(parent_row, child_row) -> list[str]:
    result, parent = [], ""
    width = max(len(parent_row or ()), len(child_row or ()))
    for index in range(width):
        top = _clean(parent_row[index] if index < len(parent_row) else "")
        child = _clean(child_row[index] if index < len(child_row) else "")
        if top:
            parent = top
        if child and parent and child != parent:
            result.append(f"{parent}_{child}")
        else:
            result.append(child or parent or f"未命名字段{index + 1}")
    return result


class MaterialRepository:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            for code, material_type, name, ai, insight, report in DEFAULT_GROUPS:
                connection.execute(
                    "INSERT OR IGNORE INTO data_group(group_id,group_code,group_name,material_type,include_ai,include_insight,include_report) VALUES(?,?,?,?,?,?,?)",
                    (code, code.removeprefix("DG-"), name, material_type, ai, insight, report),
                )
            defaults = (
                ("RULE-ESCAPE-ITR", "漏测分析关联ITR", "ESCAPE_ANALYSIS", "ITR_SOURCE", "ITR单号", "问题信息_ITR单号", "NORMALIZE_ITR", 10),
                ("RULE-CS-ITR", "彻底解决单关联ITR", "ITR_CS", "ITR_SOURCE", "问题信息_彻底解决单号", "问题信息_ITR单号", "STRIP_CS", 20),
                ("RULE-SWOPS-ITR", "软件运营数据关联ITR", "SOFTWARE_OPERATION", "ITR_SOURCE", "问题信息_彻底解决单号", "问题信息_ITR单号", "STRIP_CS", 30),
            )
            for row in defaults:
                connection.execute("INSERT OR IGNORE INTO association_rule(rule_id,rule_name,source_type,target_type,source_field,target_field,transform,priority) VALUES(?,?,?,?,?,?,?,?)", row)

    def connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def groups(self, enabled_only=True):
        where = " WHERE enabled=1" if enabled_only else ""
        with self.connect() as c:
            return [dict(x) for x in c.execute("SELECT * FROM data_group" + where + " ORDER BY group_name")]

    def group(self, group_code):
        with self.connect() as c:
            row = c.execute("SELECT * FROM data_group WHERE group_code=?", (group_code,)).fetchone()
            return dict(row) if row else None

    def add_material(self, group, business_key, raw, source_file, sheet, row_number):
        canonical = normalize_itr(business_key)
        digest = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        with self.connect() as c:
            existing = c.execute("SELECT material_id FROM source_material WHERE group_id=? AND business_key=? AND source_hash=?", (group["group_id"], business_key, digest)).fetchone()
            if existing:
                return existing[0], "SKIPPED"
            version = c.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM source_material WHERE group_id=? AND business_key=?", (group["group_id"], business_key)).fetchone()[0]
            material_id = f"MAT-{uuid.uuid4().hex}"
            c.execute("INSERT INTO source_material(material_id,group_id,material_type,business_key,canonical_itr,version_no,source_hash,source_file,sheet_name,row_number,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (material_id, group["group_id"], group["material_type"], business_key, canonical, version, digest, source_file, sheet, row_number, json.dumps(raw, ensure_ascii=False, default=str)))
            return material_id, "NEW" if version == 1 else "UPDATED"

    def refresh_links(self):
        with self.connect() as c:
            c.execute("DELETE FROM issue_material_link WHERE link_status<>'MANUAL_LINKED'")
            rules=c.execute("SELECT * FROM association_rule WHERE status='ACTIVE' ORDER BY priority").fetchall()
            allowed={}
            for rule in rules:
                if rule["source_type"]=="ESCAPE_ANALYSIS":allowed[rule["target_type"]]=rule
                else:allowed[rule["source_type"]]=rule
            materials = c.execute("SELECT material_id,material_type,canonical_itr FROM source_material WHERE canonical_itr<>''").fetchall()
            for material in materials:
                rule=allowed.get(material["material_type"])
                if not rule:continue
                issues = c.execute("SELECT knowledge_id FROM quality_issue WHERE UPPER(REPLACE(business_issue_id,' ',''))=?", (material["canonical_itr"],)).fetchall()
                status = "LINKED" if len(issues) == 1 else ("CONFLICT" if len(issues) > 1 else "ITR_NOT_FOUND")
                if len(issues) == 1:
                    c.execute("INSERT OR IGNORE INTO issue_material_link(link_id,knowledge_id,material_id,rule_id,link_status,match_value) VALUES(?,?,?,?,?,?)", (f"LNK-{uuid.uuid4().hex}", issues[0][0], material["material_id"], rule["rule_id"], status, material["canonical_itr"]))

    def materials_for_issue(self, knowledge_id):
        """Return only source materials proven to belong to one canonical issue.

        Persisted links remain authoritative.  For historical databases where
        the link table was not rebuilt during R2 assembly, an exact unique
        canonical ITR match is projected read-only.  Ambiguous or missing
        matches remain fail-closed and are never exposed as relations.
        """
        return [
            row
            for row in self.list_materials_with_issue_links(limit=100000)
            if row.get("knowledge_id") == knowledge_id
            and row.get("link_status") in {"LINKED", "MANUAL_LINKED", "DERIVED_CANONICAL_LINK"}
        ]

    def list_materials(self, group_code="", limit=200):
        sql="SELECT m.*,g.group_code,g.group_name FROM source_material m JOIN data_group g ON g.group_id=m.group_id"
        values=[]
        if group_code: sql+=" WHERE g.group_code=?";values.append(group_code)
        sql+=" ORDER BY m.created_at DESC LIMIT ?";values.append(limit)
        with self.connect() as c:return [dict(x) for x in c.execute(sql,values)]

    def list_materials_with_issue_links(self, group_code="", limit=200):
        """Read imported source rows with only established issue-material links.

        This is a projection for existing workbench pages. It does not infer a
        link from names or create another ITR/resolution master record.
        """
        sql = """SELECT m.*,g.group_code,g.group_name,l.knowledge_id,
                        l.link_status,q.business_issue_id AS linked_issue_id
                 FROM source_material m
                 JOIN data_group g ON g.group_id=m.group_id
                 LEFT JOIN issue_material_link l ON l.material_id=m.material_id
                 LEFT JOIN quality_issue q ON q.knowledge_id=l.knowledge_id"""
        values = []
        if group_code:
            sql += " WHERE g.group_code=?"
            values.append(group_code)
        sql += " ORDER BY m.created_at DESC LIMIT ?"
        values.append(limit)
        with self.connect() as connection:
            rows = [dict(row) for row in connection.execute(sql, values)]
            for row in rows:
                row["raw"] = json.loads(row.pop("raw_json") or "{}")
                if row.get("knowledge_id"):
                    row["link_status"] = row.get("link_status") or "LINKED"
                    continue
                canonical = normalize_itr(row.get("canonical_itr"))
                if not canonical:
                    row["link_status"] = "UNLINKED"
                    continue
                issues = connection.execute(
                    "SELECT knowledge_id,business_issue_id FROM quality_issue "
                    "WHERE UPPER(REPLACE(business_issue_id,' ',''))=?",
                    (canonical,),
                ).fetchall()
                if len(issues) == 1:
                    row["knowledge_id"] = issues[0]["knowledge_id"]
                    row["linked_issue_id"] = issues[0]["business_issue_id"]
                    row["link_status"] = "DERIVED_CANONICAL_LINK"
                elif len(issues) > 1:
                    row["link_status"] = "CONFLICT"
                else:
                    row["link_status"] = "ITR_NOT_FOUND"
        return rows

    def rules(self):
        with self.connect() as c:return [dict(x) for x in c.execute("SELECT * FROM association_rule ORDER BY priority,rule_name")]

    def update_rule(self, rule_id, *, source_field, target_field, transform, status):
        if transform not in {"EXACT","NORMALIZE_ITR","STRIP_CS","APPEND_CS"}:raise ValueError("INVALID_TRANSFORM")
        if status not in {"ACTIVE","INACTIVE"}:raise ValueError("INVALID_STATUS")
        with self.connect() as c:
            if not c.execute("SELECT 1 FROM association_rule WHERE rule_id=?",(rule_id,)).fetchone():raise KeyError(rule_id)
            c.execute("UPDATE association_rule SET source_field=?,target_field=?,transform=?,status=?,version_no=version_no+1 WHERE rule_id=?",(source_field.strip(),target_field.strip(),transform,status,rule_id))

    def link_preview(self):
        with self.connect() as c:
            candidates=c.execute("SELECT COUNT(DISTINCT canonical_itr) FROM source_material WHERE canonical_itr<>''").fetchone()[0]
            matched=c.execute("SELECT COUNT(DISTINCT m.canonical_itr) FROM source_material m JOIN quality_issue q ON UPPER(REPLACE(q.business_issue_id,' ',''))=m.canonical_itr WHERE m.canonical_itr<>''").fetchone()[0]
            conflicts=c.execute("SELECT COUNT(*) FROM (SELECT m.canonical_itr FROM source_material m JOIN quality_issue q ON UPPER(REPLACE(q.business_issue_id,' ',''))=m.canonical_itr GROUP BY m.canonical_itr HAVING COUNT(DISTINCT q.knowledge_id)>1)").fetchone()[0]
            return {"candidates":candidates,"matched":matched,"unmatched":max(0,candidates-matched),"conflicts":conflicts}


class MaterialImportService:
    KEY_ALIASES = {
        "ITR_SOURCE": ("问题信息_ITR单号", "ITR单号"),
        "ITR_CS": (
            "问题信息_彻底解决单号",
            "彻底解决单号",
            "问题信息_ITR单号",
            "ITR单号",
        ),
        "SOFTWARE_OPERATION": (
            "问题信息_彻底解决单号",
            "彻底解决单号",
            "问题信息_ITR单号",
            "ITR单号",
        ),
    }

    def __init__(self, repository: MaterialRepository):
        self.repository = repository

    def import_file(self, path, group_code, header_rows=2, sheet_name=""):
        group=self.repository.group(group_code)
        if not group: raise ValueError("DATA_GROUP_NOT_FOUND")
        if group["material_type"] not in {"ITR_SOURCE","ITR_CS","SOFTWARE_OPERATION"}: raise ValueError("MATERIAL_IMPORT_NOT_ENABLED")
        workbook=load_workbook(path,read_only=True,data_only=True)
        sheets=[sheet_name] if sheet_name else workbook.sheetnames
        stats={"total":0,"new":0,"updated":0,"skipped":0,"failed":0,"errors":[]}
        for name in sheets:
            sheet=workbook[name]
            rows=sheet.iter_rows(values_only=True)
            first=next(rows,())
            if int(header_rows)==2:
                second=next(rows,());headers=combine_headers(first,second);start=3
            else:
                headers=[_clean(x) or f"未命名字段{i+1}" for i,x in enumerate(first)];start=2
            for row_number,values in enumerate(rows,start=start):
                if not any(_clean(x) for x in values):continue
                raw={headers[i]:values[i] for i in range(min(len(headers),len(values)))};stats["total"]+=1
                key=next((_clean(raw.get(alias)) for alias in self.KEY_ALIASES[group["material_type"]] if _clean(raw.get(alias))),"")
                if not key:
                    stats["failed"]+=1;stats["errors"].append({"sheet":name,"row":row_number,"error":"BUSINESS_KEY_MISSING"});continue
                _,action=self.repository.add_material(group,key,raw,Path(path).name,name,row_number);stats[action.lower()]+=1
        self.repository.refresh_links()
        return stats
