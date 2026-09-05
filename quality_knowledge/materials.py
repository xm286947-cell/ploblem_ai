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
CREATE TABLE IF NOT EXISTS material_review(
 material_id TEXT PRIMARY KEY REFERENCES source_material(material_id),review_status TEXT NOT NULL DEFAULT 'DRAFT',
 analysis_summary TEXT,root_cause TEXT,improvement_action TEXT,reviewer TEXT,
 updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS source_material_reporting_year(
 material_id TEXT PRIMARY KEY REFERENCES source_material(material_id) ON DELETE CASCADE,
 reporting_year TEXT NOT NULL,year_source TEXT NOT NULL,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""


def _clean(value: Any) -> str:
    return str(value or "").strip()


def normalize_itr(value: Any) -> str:
    text = re.sub(r"\s+", "", _clean(value)).upper()
    return text[:-2] if text.endswith("CS") else text


def year_from_itr(value: Any) -> str:
    match = re.match(r"^ITR(20\d{2})", normalize_itr(value))
    return match.group(1) if match else ""


def validate_reporting_year(value: Any) -> str:
    year = _clean(value)
    if not re.fullmatch(r"20\d{2}", year) or not 2000 <= int(year) <= 2099:
        raise ValueError("考核年份必须为2000—2099之间的四位年份")
    return year


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


def _first(raw: dict[str, Any], *names: str) -> str:
    for name in names:
        value = _clean(raw.get(name))
        if value:
            return value
    return ""


def _material_view(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    raw = json.loads(item.pop("raw_json", "{}") or "{}")
    item["raw"] = raw
    item["title"] = _first(raw, "问题信息_问题主题", "问题信息_问题描述", "问题主题", "问题描述") or "未提供问题描述"
    item["month"] = _first(raw, "数据运营_KPI计入月份", "问题信息_创建月份", "创建月份") or "-"
    file_year = _first(raw, "数据运营_KPI计入年份", "数据运营_KPI计入年度", "KPI计入年份", "考核年份")
    itr_year = year_from_itr(item.get("business_key"))
    item["year"] = _clean(item.get("reporting_year")) or itr_year or file_year or "-"
    source_labels={"BATCH_MANUAL":"批量人工设置","IMPORT_MANUAL":"导入时人工设置","IMPORT_FILE":"原始文件","AUTO_ITR":"ITR编号默认"}
    item["year_source"] = source_labels.get(_clean(item.get("year_source")),_clean(item.get("year_source"))) or ("ITR编号默认" if itr_year else "原始文件" if file_year else "未设置")
    item["product"] = _first(raw, "问题信息_产品型号", "问题信息_产品类型", "产品型号", "产品类型") or "-"
    item["domain"] = _first(raw, "问题信息_问题领域", "问题领域", "问题信息_产品类型", "产品类型") or "未分类"
    item["severity"] = _first(raw, "问题信息_问题等级", "问题等级") or "-"
    item["process_status"] = _first(raw, "过程信息_当前状态", "数据运营_审核状态", "当前状态", "审核状态") or "-"
    return item


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
        self.refresh_links()

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
            try:issue_rows=c.execute("SELECT knowledge_id,business_issue_id FROM quality_issue").fetchall()
            except sqlite3.OperationalError:issue_rows=[]
            issues_by_itr={}
            for issue in issue_rows:issues_by_itr.setdefault(normalize_itr(issue['business_issue_id']),[]).append(issue['knowledge_id'])
            materials = c.execute("SELECT material_id,material_type,canonical_itr FROM source_material WHERE canonical_itr<>''").fetchall()
            for material in materials:
                rule=allowed.get(material["material_type"])
                if not rule:continue
                issues=issues_by_itr.get(normalize_itr(material['canonical_itr']),[])
                status = "LINKED" if len(issues) == 1 else ("CONFLICT" if len(issues) > 1 else "ITR_NOT_FOUND")
                if len(issues) == 1:
                    c.execute("INSERT OR IGNORE INTO issue_material_link(link_id,knowledge_id,material_id,rule_id,link_status,match_value) VALUES(?,?,?,?,?,?)", (f"LNK-{uuid.uuid4().hex}", issues[0], material["material_id"], rule["rule_id"], status, material["canonical_itr"]))

    def materials_for_issue(self, knowledge_id):
        with self.connect() as c:
            rows = c.execute("SELECT m.*,g.group_name,l.link_status FROM issue_material_link l JOIN source_material m ON m.material_id=l.material_id JOIN data_group g ON g.group_id=m.group_id WHERE l.knowledge_id=? ORDER BY m.material_type,m.version_no DESC", (knowledge_id,)).fetchall()
            result=[]
            for row in rows:
                item=dict(row); item["raw"]=json.loads(item.pop("raw_json") or "{}"); result.append(item)
            return result

    def list_materials(self, group_code="", limit=200):
        sql="SELECT m.*,g.group_code,g.group_name,y.reporting_year,y.year_source FROM source_material m JOIN data_group g ON g.group_id=m.group_id LEFT JOIN source_material_reporting_year y ON y.material_id=m.material_id"
        values=[]
        if group_code: sql+=" WHERE g.group_code=?";values.append(group_code)
        sql+=" ORDER BY m.created_at DESC LIMIT ?";values.append(limit)
        with self.connect() as c:return [dict(x) for x in c.execute(sql,values)]

    def search_materials(self, group_code, *, q="", domain="", month="", year="", page=1, page_size=20):
        rows = self.list_materials(group_code, 100000)
        items = [_material_view(row) for row in rows]
        if q:
            keyword = q.strip().lower()
            items = [item for item in items if keyword in (item["business_key"] + " " + item["canonical_itr"] + " " + item["title"] + " " + item["product"]).lower()]
        if domain:
            items = [item for item in items if domain.lower() in item["domain"].lower()]
        if month:
            items = [item for item in items if item["month"] == month]
        if year:
            items = [item for item in items if item["year"] == year]
        total = len(items); page=max(1,int(page));page_size=max(1,min(100,int(page_size)))
        start=(page-1)*page_size
        return {"items":items[start:start+page_size],"total":total,"page":page,"page_size":page_size,
                "pages":max(1,(total+page_size-1)//page_size),
                "domains":sorted({item["domain"] for item in [_material_view(row) for row in rows] if item["domain"]}),
                "months":sorted({item["month"] for item in [_material_view(row) for row in rows] if item["month"]!="-"},reverse=True),
                "years":sorted({item["year"] for item in [_material_view(row) for row in rows] if item["year"]!="-"},reverse=True)}

    def material(self, material_id):
        with self.connect() as c:
            row=c.execute("SELECT m.*,g.group_code,g.group_name,y.reporting_year,y.year_source FROM source_material m JOIN data_group g ON g.group_id=m.group_id LEFT JOIN source_material_reporting_year y ON y.material_id=m.material_id WHERE m.material_id=?",(material_id,)).fetchone()
            if not row:return None
            item=_material_view(dict(row))
            link=c.execute("SELECT knowledge_id,link_status FROM issue_material_link WHERE material_id=? ORDER BY created_at DESC LIMIT 1",(material_id,)).fetchone()
            item["linked_issue"]=dict(link) if link else None
            review=c.execute("SELECT * FROM material_review WHERE material_id=?",(material_id,)).fetchone()
            item["review"]=dict(review) if review else {"review_status":"DRAFT","analysis_summary":"","root_cause":"","improvement_action":"","reviewer":""}
            return item

    def set_reporting_year(self, material_ids, year, *, source="BATCH_MANUAL", preserve_manual=False):
        year=validate_reporting_year(year);ids=list(dict.fromkeys(material_ids or []))
        if not ids:raise ValueError("请至少选择一条软件考核问题")
        with self.connect() as c:
            rows=c.execute("SELECT m.material_id,g.group_code FROM source_material m JOIN data_group g ON g.group_id=m.group_id WHERE m.material_id IN ("+','.join('?' for _ in ids)+")",ids).fetchall()
            if len(rows)!=len(ids) or any(row['group_code']!='SW-OPS' for row in rows):raise ValueError("只能修改软件考核工作台中的问题年份")
            for material_id in ids:
                current=c.execute('SELECT year_source FROM source_material_reporting_year WHERE material_id=?',(material_id,)).fetchone()
                if preserve_manual and current and current[0] in ('BATCH_MANUAL','IMPORT_MANUAL'):continue
                c.execute("INSERT INTO source_material_reporting_year(material_id,reporting_year,year_source,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(material_id) DO UPDATE SET reporting_year=excluded.reporting_year,year_source=excluded.year_source,updated_at=CURRENT_TIMESTAMP",(material_id,year,source))
        return len(ids)

    def save_review(self, material_id, *, review_status, analysis_summary, root_cause, improvement_action, reviewer):
        if review_status not in {"DRAFT","COMPLETED"}:raise ValueError("INVALID_REVIEW_STATUS")
        with self.connect() as c:
            if not c.execute("SELECT 1 FROM source_material WHERE material_id=?",(material_id,)).fetchone():raise KeyError(material_id)
            c.execute("""INSERT INTO material_review(material_id,review_status,analysis_summary,root_cause,improvement_action,reviewer,updated_at)
              VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(material_id) DO UPDATE SET review_status=excluded.review_status,
              analysis_summary=excluded.analysis_summary,root_cause=excluded.root_cause,improvement_action=excluded.improvement_action,
              reviewer=excluded.reviewer,updated_at=CURRENT_TIMESTAMP""",(material_id,review_status,analysis_summary.strip(),root_cause.strip(),improvement_action.strip(),reviewer.strip()))

    def related_materials(self, canonical_itr, exclude_material_id=""):
        with self.connect() as c:
            rows=c.execute("SELECT m.*,g.group_code,g.group_name FROM source_material m JOIN data_group g ON g.group_id=m.group_id WHERE m.canonical_itr=? AND m.material_id<>? ORDER BY m.created_at DESC",(canonical_itr,exclude_material_id)).fetchall()
            return [_material_view(dict(row)) for row in rows]

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
            material_itrs={normalize_itr(row[0]) for row in c.execute("SELECT canonical_itr FROM source_material WHERE canonical_itr<>''")}
            issues_by_itr={}
            try:issue_rows=c.execute("SELECT knowledge_id,business_issue_id FROM quality_issue").fetchall()
            except sqlite3.OperationalError:issue_rows=[]
            for row in issue_rows:issues_by_itr.setdefault(normalize_itr(row['business_issue_id']),set()).add(row['knowledge_id'])
            matched=sum(1 for value in material_itrs if issues_by_itr.get(value))
            conflicts=sum(1 for value in material_itrs if len(issues_by_itr.get(value,set()))>1)
            return {"candidates":candidates,"matched":matched,"unmatched":max(0,candidates-matched),"conflicts":conflicts}


class MaterialImportService:
    KEY_ALIASES = {
        "ITR_SOURCE": ("问题信息_ITR单号", "ITR单号"),
        "ITR_CS": ("问题信息_彻底解决单号", "彻底解决单号"),
        "SOFTWARE_OPERATION": ("问题信息_彻底解决单号", "彻底解决单号"),
    }

    def __init__(self, repository: MaterialRepository):
        self.repository = repository

    def import_file(self, path, group_code, header_rows=2, sheet_name="", reporting_year=""):
        group=self.repository.group(group_code)
        if not group: raise ValueError("DATA_GROUP_NOT_FOUND")
        if group["material_type"] not in {"ITR_SOURCE","ITR_CS","SOFTWARE_OPERATION"}: raise ValueError("MATERIAL_IMPORT_NOT_ENABLED")
        if reporting_year:validate_reporting_year(reporting_year)
        stats={"total":0,"new":0,"updated":0,"skipped":0,"failed":0,"errors":[]}
        workbook=load_workbook(path,read_only=True,data_only=True)
        try:
            sheets=[sheet_name] if sheet_name else workbook.sheetnames
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
                    material_id,action=self.repository.add_material(group,key,raw,Path(path).name,name,row_number);stats[action.lower()]+=1
                    if group["material_type"]=="SOFTWARE_OPERATION":
                        file_year=_first(raw,"数据运营_KPI计入年份","数据运营_KPI计入年度","KPI计入年份","考核年份")
                        itr_year=year_from_itr(key);effective=reporting_year or itr_year or file_year
                        if effective:self.repository.set_reporting_year([material_id],effective,source="IMPORT_MANUAL" if reporting_year else "AUTO_ITR" if itr_year else "IMPORT_FILE",preserve_manual=not bool(reporting_year))
        finally:
            workbook.close()
        self.repository.refresh_links()
        return stats
