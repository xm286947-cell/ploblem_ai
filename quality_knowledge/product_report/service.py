from __future__ import annotations

import json
import hashlib
import re
import uuid
from collections import Counter
from typing import Any

class ProductReportError(RuntimeError):
    pass


class ProductQualityReportService:
    """Build an auditable report from objective projections; never mutates issue analysis."""

    def __init__(self, repository: Any):
        self.repository = repository
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        with self.repository.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS product_quality_report (
              report_id TEXT PRIMARY KEY, product_code TEXT NOT NULL,
              start_month TEXT NOT NULL, end_month TEXT NOT NULL,
              status TEXT NOT NULL, current_version_no INTEGER NOT NULL DEFAULT 1,
              created_by TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS product_quality_report_version (
              report_version_id TEXT PRIMARY KEY, report_id TEXT NOT NULL,
              version_no INTEGER NOT NULL, status TEXT NOT NULL,
              scope_hash TEXT NOT NULL, report_json TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(report_id) REFERENCES product_quality_report(report_id),
              UNIQUE(report_id, version_no)
            );
            """)

    def precheck(self, product_code: str, start_month: str, end_month: str) -> dict[str, Any]:
        records = self._records(product_code, start_month, end_month)
        completed = sum(1 for x in records if x["analysis_status"] == "COMPLETED")
        analysed = sum(1 for x in records if x["analysis_set_id"])
        total = len(records)
        return {
            "product_code": product_code, "start_month": start_month, "end_month": end_month,
            "issue_count": total, "analysed_count": analysed, "completed_count": completed,
            "analysis_coverage_rate": round(completed / total * 100, 1) if total else 0,
            "ready": total > 0,
            "limitations": ([] if total else ["当前范围内没有问题数据"]) +
                (["部分问题尚未完成 AI 分析，报告会明确标注覆盖率"] if total and completed < total else []),
        }

    def create(self, product_code: str, start_month: str, end_month: str, created_by: str = "") -> dict[str, Any]:
        check = self.precheck(product_code, start_month, end_month)
        if not check["ready"]:
            raise ProductReportError("REPORT_SCOPE_EMPTY")
        records = self._records(product_code, start_month, end_month)
        overview = self._scoped_overview(records)
        report = self._compose(check, overview, records)
        report_id = f"PQR-{uuid.uuid4().hex}"
        version_id = f"PQRV-{uuid.uuid4().hex}"
        with self.repository.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT INTO product_quality_report(report_id,product_code,start_month,end_month,status,created_by) VALUES(?,?,?,?,?,?)",
                      (report_id, product_code, start_month, end_month, "REVIEW_REQUIRED", created_by))
            c.execute("INSERT INTO product_quality_report_version(report_version_id,report_id,version_no,status,scope_hash,report_json) VALUES(?,?,?,?,?,?)",
                      (version_id, report_id, 1, "REVIEW_REQUIRED", overview["analysis_scope_hash"], json.dumps(report, ensure_ascii=False)))
            c.commit()
        return self.get(report_id)

    def list(self) -> list[dict[str, Any]]:
        with self.repository.connect() as c:
            rows = c.execute("SELECT * FROM product_quality_report ORDER BY created_at DESC,report_id DESC").fetchall()
        return [dict(x) for x in rows]

    def get(self, report_id: str) -> dict[str, Any]:
        with self.repository.connect() as c:
            row = c.execute("""SELECT r.*,v.report_version_id,v.scope_hash,v.report_json
              FROM product_quality_report r JOIN product_quality_report_version v
              ON v.report_id=r.report_id AND v.version_no=r.current_version_no WHERE r.report_id=?""", (report_id,)).fetchone()
        if not row:
            raise ProductReportError("REPORT_NOT_FOUND")
        result = dict(row); result["report"] = json.loads(result.pop("report_json")); return result

    def publish(self, report_id: str) -> dict[str, Any]:
        with self.repository.connect() as c:
            if not c.execute("SELECT 1 FROM product_quality_report WHERE report_id=?", (report_id,)).fetchone():
                raise ProductReportError("REPORT_NOT_FOUND")
            c.execute("UPDATE product_quality_report SET status='PUBLISHED',updated_at=CURRENT_TIMESTAMP WHERE report_id=?", (report_id,))
            c.execute("UPDATE product_quality_report_version SET status='PUBLISHED' WHERE report_id=? AND version_no=(SELECT current_version_no FROM product_quality_report WHERE report_id=?)", (report_id, report_id))
            c.commit()
        return self.get(report_id)

    def delete(self, report_id: str) -> dict[str, Any]:
        current = self.get(report_id)
        if current.get("status") == "PUBLISHED":
            raise ProductReportError("PUBLISHED_REPORT_CANNOT_BE_DELETED")
        with self.repository.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("DELETE FROM product_quality_report_version WHERE report_id=?", (report_id,))
            c.execute("DELETE FROM product_quality_report WHERE report_id=?", (report_id,))
            c.commit()
        return {"report_id": report_id, "deleted": True}

    def _records(self, product_code: str, start_month: str, end_month: str) -> list[dict[str, Any]]:
        with self.repository.connect() as c:
            rows = c.execute("""SELECT i.knowledge_id,i.business_issue_id,s.snapshot_json,a.analysis_set_id,a.status analysis_status
              FROM quality_issue i JOIN quality_issue_version v ON v.issue_version_id=i.current_version_id
              JOIN issue_normalized_snapshot s ON s.issue_version_id=v.issue_version_id
              LEFT JOIN product_config p ON p.product_id=i.product_id
              LEFT JOIN analysis_set a ON a.analysis_set_id=(SELECT x.analysis_set_id FROM analysis_set x WHERE x.issue_version_id=v.issue_version_id ORDER BY x.rowid DESC LIMIT 1)
              WHERE (p.product_code=? OR json_extract(s.snapshot_json,'$.ISSUE_FACT.product')=?)
              ORDER BY i.knowledge_id""", (product_code, product_code)).fetchall()
            result=[]
            for row in rows:
                item=dict(row); item["snapshot"]=json.loads(item.pop("snapshot_json") or "{}")
                aid=item["analysis_set_id"]
                item["gaps"]=[dict(x) for x in c.execute("SELECT capability_axis,capability_code,governance_scope,details_json FROM issue_capability_gap WHERE analysis_set_id=?",(aid,))] if aid else []
                item["mrc"]=[dict(x) for x in c.execute("SELECT side,mrc_code,role FROM issue_mrc WHERE analysis_set_id=?",(aid,))] if aid else []
                month = str(item["snapshot"].get("ISSUE_FACT", {}).get("month") or "")
                if self._month_number(start_month) <= self._month_number(month) <= self._month_number(end_month):
                    result.append(item)
        return result

    @staticmethod
    def _month_number(value: str) -> int:
        match = re.search(r"\d+", str(value or ""))
        return int(match.group()) if match else -1

    def _scoped_overview(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        counts: Counter[tuple[str, str, str]] = Counter()
        for record in records:
            if record["analysis_status"] != "COMPLETED":
                continue
            for gap in record["gaps"]:
                counts[(gap["capability_axis"], gap["capability_code"], gap["governance_scope"])] += 1
        labels: dict[str, str] = {}
        with self.repository.connect() as c:
            for row in c.execute("SELECT code,label_zh FROM analysis_taxonomy_term WHERE enabled=1"):
                labels[row["code"]] = row["label_zh"]
        grouped: dict[str, list[dict[str, Any]]] = {"QUALITY_ENGINEERING": [], "QUALITY_MANAGEMENT": []}
        denominator = max(len(records), 1)
        for (axis, code, scope), count in counts.items():
            item = {"capability_axis": axis, "capability_code": code, "capability_label_zh": labels.get(code, code),
                    "governance_scope": scope, "issue_count": count, "score": round(count / denominator * 100, 1),
                    "contradiction_key": f"{axis}:{code}:{scope}", "control_status_distribution": {}}
            grouped.setdefault(axis, []).append(item)
        for items in grouped.values(): items.sort(key=lambda x: (-x["score"], -x["issue_count"], x["capability_code"]))
        digest = hashlib.sha256("|".join(r["knowledge_id"] + ":" + str(r["analysis_set_id"]) for r in records).encode()).hexdigest()
        return {"analysis_scope_hash": digest, "quality_engineering_top3": grouped.get("QUALITY_ENGINEERING", [])[:3],
                "quality_management_top3": grouped.get("QUALITY_MANAGEMENT", [])[:3]}

    def _compose(self, check: dict[str, Any], overview: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
        fact = [r["snapshot"].get("ISSUE_FACT", {}) for r in records]
        severity = Counter(str(x.get("severity") or "UNKNOWN").upper() for x in fact)
        mrc = Counter(x["mrc_code"] for r in records for x in r["mrc"] if x.get("role") == "PRIMARY")
        contradictions = (overview.get("quality_engineering_top3") or []) + (overview.get("quality_management_top3") or [])
        top = []
        for index, item in enumerate(sorted(contradictions, key=lambda x: x.get("score", 0), reverse=True)[:3], 1):
            top.append({"rank": index, "name": item.get("capability_label_zh") or item.get("capability_code"),
                        "axis": item.get("capability_axis"), "risk_score": item.get("score", 0),
                        "issue_count": item.get("issue_count", 0), "control_status": item.get("control_status_distribution", {}),
                        "semantic_key": item.get("contradiction_key"), "judgement": "该能力缺口在当前范围内重复出现，应优先核验证据并建立可验证控制。"})
        ids=[{"knowledge_id":r["knowledge_id"],"business_issue_id":r["business_issue_id"]} for r in records]
        return {"schema_version":"PRODUCT_QUALITY_REPORT_MVP_V1","scope":check,
                "overall_judgement":f"共识别 {len(records)} 个问题，完成分析覆盖率 {check['analysis_coverage_rate']}%。当前应优先治理重复出现且控制不足的工程与管理能力缺口。",
                "risk_summary":{"severity_distribution":dict(severity),"high_risk_count":severity.get("H",0)+severity.get("HIGH",0),"primary_mrc_top5":mrc.most_common(5)},
                "engineering_quality_summary":overview.get("quality_engineering_top3",[]),
                "quality_management_summary":overview.get("quality_management_top3",[]),
                "core_contradictions":top,
                "governance_priorities":[{"priority":"P0","action":"复核高风险问题与现行控制，立即补齐遏制措施","validation":"高风险问题均有责任角色、措施和验证证据"},{"priority":"P1","action":"针对 TOP3 矛盾修复流程或工程机制","validation":"同类问题新增率与流出率持续下降"},{"priority":"P2","action":"沉淀标准、工具与横向复制机制","validation":"控制覆盖到相关产品和生命周期阶段"}],
                "manual_confirmation_questions":["TOP3 矛盾是否符合业务实际？","高风险问题是否已有有效遏制措施？","治理责任角色和验证周期是否明确？"],
                "evidence_issues":ids[:50],"limitations":check["limitations"]}
