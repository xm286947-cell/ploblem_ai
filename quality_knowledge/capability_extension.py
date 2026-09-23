"""Append-only quality-capability projection for the stable V1 application.

The extension deliberately owns only ``qc_*`` tables.  Existing issue,
mapping, import, AI and human-analysis tables remain the system of record.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from quality_knowledge.analysis_profiles import DOMAIN_PROFILES, ISSUE_TYPES, LIFECYCLE_PHASES

SOURCE_LABELS={"HUMAN_CONFIRMED":"人工确认","SOURCE_DATA":"原始数据","AI_STANDARDIZED":"AI 标准化","AI_INFERRED":"AI 推断"}
SOURCE_PRIORITY={"HUMAN_CONFIRMED":4,"SOURCE_DATA":3,"AI_STANDARDIZED":2,"AI_INFERRED":1}
STANDARD_TAGS={
    "DOMAIN":set(DOMAIN_PROFILES)-{"AUTO"},
    "LIFECYCLE":(set(LIFECYCLE_PHASES)-{"AUTO"})|{"IMPLEMENTATION"},
    "ISSUE_TYPE":set(ISSUE_TYPES),
}
TAG_ALIASES={
    ("ISSUE_TYPE","FUNCTIONAL"):"FUNCTIONAL_DEFECT",
    ("LIFECYCLE","DESIGN"):"SOLUTION_DESIGN",
    ("LIFECYCLE","INTEGRATION"):"INTEGRATION_TEST",
}


SCHEMA = """
CREATE TABLE IF NOT EXISTS qc_analysis_projection(
 projection_id TEXT PRIMARY KEY, knowledge_id TEXT NOT NULL, issue_version_id TEXT NOT NULL,
 source_fingerprint TEXT NOT NULL, contract_version TEXT NOT NULL DEFAULT 'QC-EXT-V1',
 projected_at TEXT DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(knowledge_id, issue_version_id, source_fingerprint));
CREATE TABLE IF NOT EXISTS qc_issue_tag(
 projection_id TEXT NOT NULL, axis TEXT NOT NULL, tag_code TEXT NOT NULL,
 source_type TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0,
 PRIMARY KEY(projection_id, axis, tag_code));
CREATE TABLE IF NOT EXISTS qc_issue_mrc(
 projection_id TEXT NOT NULL, side TEXT NOT NULL, mrc_code TEXT NOT NULL,
 role TEXT NOT NULL DEFAULT 'PRIMARY', control_status TEXT NOT NULL DEFAULT 'UNKNOWN',
 source_type TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0,
 PRIMARY KEY(projection_id, side, mrc_code, role));
CREATE TABLE IF NOT EXISTS qc_analysis_evidence(
 evidence_id TEXT PRIMARY KEY, projection_id TEXT NOT NULL, stage TEXT NOT NULL,
 target_path TEXT NOT NULL, excerpt TEXT, source_type TEXT NOT NULL,
 confidence REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS qc_human_revision(
 revision_id TEXT PRIMARY KEY, knowledge_id TEXT NOT NULL, issue_version_id TEXT NOT NULL,
 target_path TEXT NOT NULL, original_value_json TEXT, confirmed_value_json TEXT NOT NULL,
 status TEXT NOT NULL, reason TEXT, evidence TEXT, confirmed_by TEXT NOT NULL,
 confirmed_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS qc_issue_hardware_component(
 component_id TEXT PRIMARY KEY, projection_id TEXT, knowledge_id TEXT NOT NULL, issue_version_id TEXT NOT NULL,
 relevance TEXT NOT NULL, component_category TEXT, component_name TEXT, manufacturer TEXT,
 model_part_number TEXT, lot_batch TEXT, serial_number TEXT, board_module TEXT,
 reference_designator TEXT, installation_location TEXT, hardware_version TEXT,
 source_type TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS qc_hardware_failure_analysis(
 failure_analysis_id TEXT PRIMARY KEY, component_id TEXT NOT NULL, failure_mode TEXT,
 failure_mechanism TEXT, failure_cause TEXT, customer_impact TEXT, reproduction_condition TEXT,
 detection_method TEXT, disposition TEXT, source_type TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0,
 evidence_json TEXT NOT NULL DEFAULT '[]');
CREATE INDEX IF NOT EXISTS idx_qc_projection_issue ON qc_analysis_projection(knowledge_id,issue_version_id);
CREATE INDEX IF NOT EXISTS idx_qc_mrc_code ON qc_issue_mrc(side,mrc_code);
CREATE INDEX IF NOT EXISTS idx_qc_tag_axis ON qc_issue_tag(axis,tag_code);
CREATE INDEX IF NOT EXISTS idx_qc_revision_issue ON qc_human_revision(knowledge_id,issue_version_id,target_path,confirmed_at);
CREATE INDEX IF NOT EXISTS idx_qc_hardware_issue ON qc_issue_hardware_component(knowledge_id,issue_version_id,relevance);
"""


def _value(raw):
    if isinstance(raw, dict):
        return raw.get("value") or raw.get("description") or ""
    return raw or ""


def _confidence(raw, fallback=0.0):
    if isinstance(raw, dict):
        try:
            return float(raw.get("confidence") or fallback)
        except (TypeError, ValueError):
            return fallback
    return fallback


class QualityCapabilityExtension:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def project_issue(self, issue: dict, analysis: dict) -> dict:
        """Project existing V1 AI results without changing their source rows."""
        version_id = str(issue.get("issue_version_id") or "")
        knowledge_id = str(issue.get("knowledge_id") or "")
        payload = {
            key: ((value or {}).get("analysis_run_id"), (value or {}).get("result"))
            for key, value in sorted((analysis or {}).items())
        }
        fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        projection_id = "QCP-" + hashlib.sha256(f"{knowledge_id}|{version_id}|{fingerprint}".encode()).hexdigest()[:24]
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT projection_id FROM qc_analysis_projection WHERE projection_id=?", (projection_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO qc_analysis_projection(projection_id,knowledge_id,issue_version_id,source_fingerprint) VALUES(?,?,?,?)",
                    (projection_id, knowledge_id, version_id, fingerprint),
                )
                self._insert_results(connection, projection_id, issue, analysis)
                connection.commit()
        return self.get_issue(knowledge_id, version_id)

    def _insert_results(self, connection, projection_id, issue, analysis):
        domain = str(issue.get("issue_domain") or "").upper()
        if domain and domain != "AUTO":
            connection.execute(
                "INSERT OR IGNORE INTO qc_issue_tag VALUES(?,?,?,?,?)",
                (projection_id, "DOMAIN", domain, "SOURCE_DATA", 1.0),
            )
        occurrence = ((analysis.get("occurrence") or {}).get("result") or {})
        escape = ((analysis.get("escape") or {}).get("result") or {})
        lifecycle_terms = list(occurrence.get("lifecycle_tags") or []) + list(escape.get("lifecycle_tags") or [])
        lifecycle_terms += [x for x in (occurrence.get("introduced_phase"), escape.get("expected_detection_stage"), escape.get("actual_detection_stage")) if x]
        for term in lifecycle_terms:
            raw = term if isinstance(term, dict) else {"code": term}
            code = str(raw.get("code") or "").upper()
            if code and code not in {"AUTO", "UNKNOWN"}:
                connection.execute(
                    "INSERT OR IGNORE INTO qc_issue_tag VALUES(?,?,?,?,?)",
                    (projection_id, "LIFECYCLE", code, str(raw.get("source_type") or "AI_STANDARDIZED"),
                     float(raw.get("confidence") or 0.7)),
                )
        for term in occurrence.get("issue_type_tags") or []:
            raw = term if isinstance(term, dict) else {"code": term}
            code = str(raw.get("code") or "").upper()
            if code:
                connection.execute(
                    "INSERT OR IGNORE INTO qc_issue_tag VALUES(?,?,?,?,?)",
                    (projection_id, "ISSUE_TYPE", code, str(raw.get("source_type") or "AI_STANDARDIZED"),
                     float(raw.get("confidence") or 0)),
                )
        for side, result, category_key, summary_key in (
            ("OCCURRENCE", occurrence, "occurrence_category", "root_cause_summary"),
            ("ESCAPE", escape, "escape_category", "escape_cause_summary"),
        ):
            native_mrc = result.get("mrc") if isinstance(result.get("mrc"), dict) else {}
            code = str(native_mrc.get("code") or result.get(category_key) or "").upper()
            summary = result.get(summary_key)
            confidence = float(native_mrc.get("confidence") or _confidence(summary, float(result.get("confidence") or 0)))
            if code and code != "UNKNOWN":
                connection.execute(
                    "INSERT OR IGNORE INTO qc_issue_mrc VALUES(?,?,?,?,?,?,?)",
                    (projection_id, side, code, "PRIMARY", str(native_mrc.get("control_status") or "UNKNOWN"),
                     str(native_mrc.get("source_type") or "AI_STANDARDIZED"), confidence),
                )
            excerpt = str(_value(summary) or "").strip()
            if excerpt:
                evidence_id = "QCE-" + hashlib.sha256(f"{projection_id}|{side}|{excerpt}".encode()).hexdigest()[:24]
                connection.execute(
                    "INSERT OR IGNORE INTO qc_analysis_evidence VALUES(?,?,?,?,?,?,?)",
                    (evidence_id, projection_id, side, f"{side.lower()}.mrc.primary", excerpt,
                     "AI_INFERRED", confidence),
                )
        for index, component in enumerate(occurrence.get("hardware_components") or []):
            if not isinstance(component,dict):
                continue
            component_id = "QCH-" + hashlib.sha256(f"{projection_id}|{index}|{json.dumps(component,sort_keys=True,ensure_ascii=False)}".encode()).hexdigest()[:24]
            connection.execute(
                """INSERT OR IGNORE INTO qc_issue_hardware_component(
                     component_id,projection_id,knowledge_id,issue_version_id,relevance,component_category,
                     component_name,manufacturer,model_part_number,lot_batch,serial_number,board_module,
                     reference_designator,installation_location,hardware_version,source_type,confidence)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (component_id,projection_id,issue["knowledge_id"],issue["issue_version_id"],
                 component.get("relevance") or occurrence.get("hardware_relevance") or "POSSIBLE",
                 component.get("component_category"),component.get("component_name"),component.get("manufacturer"),
                 component.get("model_part_number"),component.get("lot_batch"),component.get("serial_number"),
                 component.get("board_module"),component.get("reference_designator"),component.get("installation_location"),
                 component.get("hardware_version"),component.get("source_type") or "AI_INFERRED",float(component.get("confidence") or 0)),
            )
            connection.execute(
                """INSERT OR IGNORE INTO qc_hardware_failure_analysis(
                     failure_analysis_id,component_id,failure_mode,failure_mechanism,failure_cause,customer_impact,
                     reproduction_condition,detection_method,disposition,source_type,confidence,evidence_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("QCHF-"+component_id[4:],component_id,component.get("failure_mode"),component.get("failure_mechanism"),
                 component.get("failure_cause"),component.get("customer_impact"),component.get("reproduction_condition"),
                 component.get("detection_method"),component.get("disposition"),component.get("source_type") or "AI_INFERRED",
                 float(component.get("confidence") or 0),json.dumps(component.get("evidence") or [],ensure_ascii=False)),
            )

    def get_issue(self, knowledge_id: str, issue_version_id: str = "") -> dict:
        with self.connect() as connection:
            params = [knowledge_id]
            clause = "p.knowledge_id=?"
            if issue_version_id:
                clause += " AND p.issue_version_id=?"
                params.append(issue_version_id)
            projection = connection.execute(
                f"SELECT * FROM qc_analysis_projection p WHERE {clause} ORDER BY projected_at DESC,rowid DESC LIMIT 1", params
            ).fetchone()
            if projection is None:
                return {"projection": None, "tags": [], "mrc": [], "evidence": []}
            pid = projection["projection_id"]
            result = {
                "projection": dict(projection),
                "tags": [dict(x) for x in connection.execute("SELECT * FROM qc_issue_tag WHERE projection_id=? ORDER BY axis,tag_code", (pid,))],
                "mrc": [dict(x) for x in connection.execute("SELECT * FROM qc_issue_mrc WHERE projection_id=? ORDER BY side,role", (pid,))],
                "evidence": [dict(x) for x in connection.execute("SELECT * FROM qc_analysis_evidence WHERE projection_id=? ORDER BY stage,target_path", (pid,))],
            }
            revisions = [dict(x) for x in connection.execute(
                """SELECT * FROM qc_human_revision WHERE knowledge_id=? AND issue_version_id=?
                    ORDER BY confirmed_at DESC,rowid DESC""", (knowledge_id, projection["issue_version_id"])
            )]
            latest = {}
            for revision in revisions:
                latest.setdefault(revision["target_path"], revision)
            result["human_revisions"] = list(latest.values())
            result["hardware_components"] = [dict(x) for x in connection.execute(
                """SELECT c.*,f.failure_mode,f.failure_mechanism,f.failure_cause,f.customer_impact,
                          f.reproduction_condition,f.detection_method,f.disposition,f.evidence_json
                     FROM qc_issue_hardware_component c LEFT JOIN qc_hardware_failure_analysis f ON f.component_id=c.component_id
                    WHERE c.knowledge_id=? AND c.issue_version_id=? AND (c.projection_id=? OR c.projection_id IS NULL)
                    ORDER BY c.created_at,c.component_id""",
                (knowledge_id,projection["issue_version_id"],pid),
            )]
            result["effective_mrc"] = {x["side"]: dict(x) for x in result["mrc"] if x["role"] == "PRIMARY"}
            for target_path, revision in latest.items():
                if revision["status"] not in {"CONFIRMED", "CORRECTED"}:
                    continue
                side = {"occurrence.mrc.primary": "OCCURRENCE", "escape.mrc.primary": "ESCAPE"}.get(target_path)
                value = json.loads(revision["confirmed_value_json"] or "null")
                if side and value:
                    current = result["effective_mrc"].get(side, {"side": side, "role": "PRIMARY"})
                    current.update({"mrc_code": value.get("code") if isinstance(value,dict) else str(value), "source_type": "HUMAN_CONFIRMED", "confidence": 1.0, "human_revision_id": revision["revision_id"]})
                    result["effective_mrc"][side] = current
            for value in result["effective_mrc"].values():
                value["source_label"]=SOURCE_LABELS.get(value.get("source_type"),value.get("source_type") or "未知来源")
            valid=[];pending=[];seen=set()
            for tag in result["tags"]:
                axis=str(tag.get("axis") or "").upper();code=str(tag.get("tag_code") or "").upper().strip()
                words=code.split()
                if len(words)%2==0 and words[:len(words)//2]==words[len(words)//2:]:
                    code=" ".join(words[:len(words)//2])
                code=TAG_ALIASES.get((axis,code),code)
                tag["tag_code"]=code
                key=(axis,code)
                if not code or key in seen:
                    continue
                seen.add(key);tag["source_label"]=SOURCE_LABELS.get(tag.get("source_type"),tag.get("source_type") or "未知来源")
                if code in STANDARD_TAGS.get(axis,set()) and float(tag.get("confidence") or 0)>=0.5:
                    valid.append(tag)
                else:
                    tag["pending_reason"]="非标准标签" if code not in STANDARD_TAGS.get(axis,set()) else "置信度不足"
                    pending.append(tag)
            grouped={}
            for tag in valid:
                grouped.setdefault(tag["axis"],[]).append(tag)
            effective_tags={}
            for axis,tags in grouped.items():
                tags.sort(key=lambda x:(SOURCE_PRIORITY.get(x.get("source_type"),0),float(x.get("confidence") or 0)),reverse=True)
                effective_tags[axis]={"primary":tags[0],"related":tags[1:4]}
            result["effective_tags"]=effective_tags
            result["pending_tags"]=pending
            for evidence in result["evidence"]:
                evidence["source_label"]=SOURCE_LABELS.get(evidence.get("source_type"),evidence.get("source_type") or "未知来源")
            return result

    def save_human_revision(self, *, knowledge_id: str, issue_version_id: str, target_path: str,
                            original_value, confirmed_value, status: str, reason: str = "",
                            evidence: str = "", confirmed_by: str) -> dict:
        if target_path not in {"occurrence.mrc.primary", "escape.mrc.primary", "lifecycle.primary"}:
            raise ValueError("QC_REVISION_TARGET_NOT_ALLOWED")
        status = status.upper()
        if status not in {"CONFIRMED", "CORRECTED", "UNRESOLVED", "NOT_APPLICABLE"}:
            raise ValueError("QC_REVISION_STATUS_INVALID")
        if not confirmed_by.strip():
            raise ValueError("QC_REVISION_ACTOR_REQUIRED")
        revision_id = "QCR-" + hashlib.sha256(
            f"{knowledge_id}|{issue_version_id}|{target_path}|{json.dumps(confirmed_value,sort_keys=True,ensure_ascii=False)}|{confirmed_by}".encode()
        ).hexdigest()[:24]
        with self.connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO qc_human_revision(
                     revision_id,knowledge_id,issue_version_id,target_path,original_value_json,
                     confirmed_value_json,status,reason,evidence,confirmed_by,confirmed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                (revision_id,knowledge_id,issue_version_id,target_path,json.dumps(original_value,ensure_ascii=False),
                 json.dumps(confirmed_value,ensure_ascii=False),status,reason,evidence,confirmed_by.strip()),
            )
            connection.commit()
        return {"revision_id":revision_id,"status":status,"target_path":target_path}

    def add_hardware_component(self, *, knowledge_id: str, issue_version_id: str, values: dict, actor: str) -> dict:
        if not actor.strip(): raise ValueError("QC_HARDWARE_ACTOR_REQUIRED")
        identity=json.dumps(values,sort_keys=True,ensure_ascii=False)
        component_id="QCH-H-"+hashlib.sha256(f"{knowledge_id}|{issue_version_id}|{identity}".encode()).hexdigest()[:20]
        relevance=str(values.get('relevance') or 'CONFIRMED').upper()
        if relevance not in {'POSSIBLE','CONFIRMED'}: raise ValueError("QC_HARDWARE_RELEVANCE_INVALID")
        with self.connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO qc_issue_hardware_component(
                     component_id,projection_id,knowledge_id,issue_version_id,relevance,component_category,
                     component_name,manufacturer,model_part_number,lot_batch,serial_number,board_module,
                     reference_designator,installation_location,hardware_version,source_type,confidence,created_at)
                   VALUES(?,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,'HUMAN_CONFIRMED',1,CURRENT_TIMESTAMP)""",
                (component_id,knowledge_id,issue_version_id,relevance,values.get('component_category'),values.get('component_name'),
                 values.get('manufacturer'),values.get('model_part_number'),values.get('lot_batch'),values.get('serial_number'),
                 values.get('board_module'),values.get('reference_designator'),values.get('installation_location'),values.get('hardware_version')),
            )
            connection.execute(
                """INSERT OR REPLACE INTO qc_hardware_failure_analysis(
                     failure_analysis_id,component_id,failure_mode,failure_mechanism,failure_cause,customer_impact,
                     reproduction_condition,detection_method,disposition,source_type,confidence,evidence_json)
                   VALUES(?,?,?,?,?,?,?,?,?,'HUMAN_CONFIRMED',1,?)""",
                ('QCHF-'+component_id[4:],component_id,values.get('failure_mode'),values.get('failure_mechanism'),
                 values.get('failure_cause'),values.get('customer_impact'),values.get('reproduction_condition'),
                 values.get('detection_method'),values.get('disposition'),json.dumps([{'actor':actor,'evidence':values.get('evidence') or ''}],ensure_ascii=False)),
            )
            connection.commit()
        return {'component_id':component_id,'source_type':'HUMAN_CONFIRMED'}

    def matrices(self, business_type: str = "", knowledge_ids=None) -> dict:
        business_clause = ""
        params = []
        if business_type:
            business_clause = " AND q.business_type=?"
            params.append(business_type.upper())
        if knowledge_ids is not None:
            knowledge_ids=list(knowledge_ids) or ['__NO_SCOPE_MATCH__']
            business_clause += " AND q.knowledge_id IN ("+','.join('?' for _ in knowledge_ids)+")"
            params.extend(knowledge_ids)
        with self.connect() as connection:
            mrc_rows = connection.execute(
                """WITH revisions AS (
                       SELECT r.*,ROW_NUMBER() OVER(
                         PARTITION BY knowledge_id,issue_version_id,target_path ORDER BY confirmed_at DESC,r.rowid DESC
                       ) position FROM qc_human_revision r
                        WHERE status IN ('CONFIRMED','CORRECTED')
                     ), effective_mrc AS (
                       SELECT p.projection_id,p.knowledge_id,
                              COALESCE(json_extract(r.confirmed_value_json,'$.code'),m.mrc_code) mrc_code
                         FROM qc_analysis_projection p
                         JOIN qc_issue_mrc m ON m.projection_id=p.projection_id AND m.role='PRIMARY'
                         LEFT JOIN revisions r ON r.knowledge_id=p.knowledge_id
                          AND r.issue_version_id=p.issue_version_id AND r.position=1
                          AND r.target_path=lower(m.side)||'.mrc.primary'
                     )
                   SELECT m.mrc_code,g.dimension capability_axis,g.category capability_code,
                          COUNT(DISTINCT p.knowledge_id) issue_count
                     FROM qc_analysis_projection p
                     JOIN effective_mrc m ON m.projection_id=p.projection_id
                     JOIN quality_issue q ON q.knowledge_id=p.knowledge_id
                     JOIN issue_capability_gap g ON g.knowledge_id=p.knowledge_id
                    WHERE 1=1""" + business_clause +
                " GROUP BY m.mrc_code,g.dimension,g.category ORDER BY issue_count DESC LIMIT 30", params
            ).fetchall()
            lifecycle_rows = connection.execute(
                """SELECT t.tag_code lifecycle_code,g.dimension capability_axis,g.category capability_code,
                          COUNT(DISTINCT p.knowledge_id) issue_count
                     FROM qc_analysis_projection p
                     JOIN qc_issue_tag t ON t.projection_id=p.projection_id AND t.axis='LIFECYCLE'
                     JOIN quality_issue q ON q.knowledge_id=p.knowledge_id
                     JOIN issue_capability_gap g ON g.knowledge_id=p.knowledge_id
                    WHERE 1=1""" + business_clause +
                " GROUP BY t.tag_code,g.dimension,g.category ORDER BY issue_count DESC LIMIT 30", params
            ).fetchall()
        return {"mrc_x_capability": [dict(x) for x in mrc_rows], "lifecycle_x_capability": [dict(x) for x in lifecycle_rows]}

    def matrix_issue_ids(self, *, matrix_axis: str, axis_code: str, capability_axis: str,
                         capability_code: str, business_type: str = "") -> list[str]:
        """Resolve a matrix cell server-side so URLs stay semantic and compact."""
        matrix_axis = matrix_axis.upper()
        params = [capability_axis.upper(), capability_code]
        business_clause = ""
        if business_type:
            business_clause = " AND q.business_type=?"
        with self.connect() as connection:
            if matrix_axis == "MRC":
                params.insert(0, axis_code.upper())
                if business_type: params.append(business_type.upper())
                rows = connection.execute(
                    """WITH revisions AS (
                           SELECT r.*,ROW_NUMBER() OVER(PARTITION BY knowledge_id,issue_version_id,target_path
                             ORDER BY confirmed_at DESC,r.rowid DESC) position
                             FROM qc_human_revision r WHERE status IN ('CONFIRMED','CORRECTED')
                         ), effective AS (
                           SELECT p.projection_id,p.knowledge_id,p.issue_version_id,
                                  COALESCE(json_extract(r.confirmed_value_json,'$.code'),m.mrc_code) code
                             FROM qc_analysis_projection p JOIN qc_issue_mrc m ON m.projection_id=p.projection_id AND m.role='PRIMARY'
                             LEFT JOIN revisions r ON r.knowledge_id=p.knowledge_id AND r.issue_version_id=p.issue_version_id
                              AND r.position=1 AND r.target_path=lower(m.side)||'.mrc.primary')
                       SELECT DISTINCT e.knowledge_id FROM effective e
                       JOIN quality_issue q ON q.knowledge_id=e.knowledge_id AND q.current_version_id=e.issue_version_id
                       JOIN issue_capability_gap g ON g.knowledge_id=e.knowledge_id AND g.issue_version_id=e.issue_version_id
                       JOIN analysis_run ar ON ar.analysis_run_id=g.analysis_run_id AND ar.status='COMPLETED'
                       WHERE e.code=? AND g.dimension=? AND g.category=?""" + business_clause,
                    params,
                ).fetchall()
            elif matrix_axis == "LIFECYCLE":
                params.insert(0, axis_code.upper())
                if business_type: params.append(business_type.upper())
                rows = connection.execute(
                    """SELECT DISTINCT p.knowledge_id FROM qc_analysis_projection p
                       JOIN qc_issue_tag t ON t.projection_id=p.projection_id AND t.axis='LIFECYCLE'
                       JOIN quality_issue q ON q.knowledge_id=p.knowledge_id AND q.current_version_id=p.issue_version_id
                       JOIN issue_capability_gap g ON g.knowledge_id=p.knowledge_id AND g.issue_version_id=p.issue_version_id
                       JOIN analysis_run ar ON ar.analysis_run_id=g.analysis_run_id AND ar.status='COMPLETED'
                       WHERE t.tag_code=? AND g.dimension=? AND g.category=?""" + business_clause,
                    params,
                ).fetchall()
            else:
                raise ValueError("QC_MATRIX_AXIS_INVALID")
        return [row[0] for row in rows]

    def insight_summary(self, business_type: str = "", knowledge_ids=None) -> dict:
        clause = ""
        params = []
        if business_type:
            clause = " WHERE q.business_type=?"
            params.append(business_type.upper())
        if knowledge_ids is not None:
            knowledge_ids=list(knowledge_ids) or ['__NO_SCOPE_MATCH__']
            clause += (" AND " if clause else " WHERE ")+"q.knowledge_id IN ("+','.join('?' for _ in knowledge_ids)+")"
            params.extend(knowledge_ids)
        with self.connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM quality_issue q" + clause, params).fetchone()[0]
            projected = connection.execute(
                "SELECT COUNT(DISTINCT p.knowledge_id) FROM qc_analysis_projection p JOIN quality_issue q ON q.knowledge_id=p.knowledge_id" + clause,
                params,
            ).fetchone()[0]
            classified = connection.execute(
                "SELECT COUNT(DISTINCT m.projection_id) FROM qc_issue_mrc m JOIN qc_analysis_projection p ON p.projection_id=m.projection_id JOIN quality_issue q ON q.knowledge_id=p.knowledge_id" + clause,
                params,
            ).fetchone()[0]
            confirmed = connection.execute(
                "SELECT COUNT(DISTINCT r.knowledge_id) FROM qc_human_revision r JOIN quality_issue q ON q.knowledge_id=r.knowledge_id" + clause,
                params,
            ).fetchone()[0]
            hardware = connection.execute(
                """SELECT COALESCE(NULLIF(c.component_category,''),'未分类器件') category,
                          COUNT(DISTINCT c.knowledge_id) issue_count
                     FROM qc_issue_hardware_component c JOIN quality_issue q ON q.knowledge_id=c.knowledge_id""" + clause +
                " GROUP BY category ORDER BY issue_count DESC LIMIT 8", params,
            ).fetchall()
            failures = connection.execute(
                """SELECT COALESCE(NULLIF(f.failure_cause,''),NULLIF(f.failure_mode,''),'待确认') category,
                          COUNT(DISTINCT c.knowledge_id) issue_count
                     FROM qc_hardware_failure_analysis f JOIN qc_issue_hardware_component c ON c.component_id=f.component_id
                     JOIN quality_issue q ON q.knowledge_id=c.knowledge_id""" + clause +
                " GROUP BY category ORDER BY issue_count DESC LIMIT 8", params,
            ).fetchall()
        return {
            "coverage": {
                "total_issues": total, "projected_issues": projected, "classified_issues": classified,
                "human_confirmed_issues": confirmed,
                "projection_rate": round(projected * 100 / (total or 1), 1),
                "classification_rate": round(classified * 100 / (total or 1), 1),
                "human_confirmation_rate": round(confirmed * 100 / (total or 1), 1),
            },
            "hardware_categories": [dict(row) for row in hardware],
            "hardware_failure_causes": [dict(row) for row in failures],
        }

    def classification_comparison(self, business_type: str = "", knowledge_ids=None) -> dict:
        """Compare source L1-L4 text with the current AI/human MRC without inventing equivalence."""
        clause = " WHERE q.business_type=?" if business_type else ""
        params = [business_type.upper()] if business_type else []
        if knowledge_ids is not None:
            knowledge_ids=list(knowledge_ids) or ['__NO_SCOPE_MATCH__']
            clause += (" AND " if clause else " WHERE ")+"q.knowledge_id IN ("+','.join('?' for _ in knowledge_ids)+")"
            params.extend(knowledge_ids)
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT q.knowledge_id,q.current_version_id,v.normalized_json
                     FROM quality_issue q JOIN quality_issue_version v ON v.issue_version_id=q.current_version_id""" + clause,
                params,
            ).fetchall()
        summary={side:{"CONSISTENT":0,"CONFLICT":0,"AI_SUPPLEMENT":0,"SOURCE_ONLY":0,"MISSING":0,"total":0}
                 for side in ("OCCURRENCE","ESCAPE")}
        examples=[]
        for row in rows:
            normalized=json.loads(row["normalized_json"] or "{}")
            projected=self.get_issue(row["knowledge_id"],row["current_version_id"])
            effective=projected.get("effective_mrc") or {}
            for side,fact,prefix in (("OCCURRENCE","occurrence","cause_l"),("ESCAPE","escape","escape_l")):
                source=[str((normalized.get(fact) or {}).get(f"{prefix}{i}") or '').strip() for i in range(1,5)]
                source=[x for x in source if x]
                ai=str((effective.get(side) or {}).get("mrc_code") or '').strip()
                if source and ai:
                    normalized_source={x.upper().replace(' ','_') for x in source}
                    state="CONSISTENT" if ai.upper() in normalized_source else "CONFLICT"
                elif ai: state="AI_SUPPLEMENT"
                elif source: state="SOURCE_ONLY"
                else: state="MISSING"
                summary[side][state]+=1; summary[side]["total"]+=1
                if len(examples)<12 and state in {"CONFLICT","AI_SUPPLEMENT","SOURCE_ONLY"}:
                    examples.append({"knowledge_id":row["knowledge_id"],"side":side,"state":state,"source":" / ".join(source),"ai":ai})
        return {"sides":summary,"examples":examples,"method":"SOURCE_L1_L4_VS_EFFECTIVE_MRC_EXACT_CODE"}

    def filter_issue_ids_by_lifecycle(self, knowledge_ids, lifecycle_code: str) -> list[str]:
        ids=list(knowledge_ids or [])
        if not lifecycle_code or not ids:
            return ids
        with self.connect() as connection:
            rows=connection.execute(
                """SELECT DISTINCT p.knowledge_id FROM qc_analysis_projection p
                     JOIN qc_issue_tag t ON t.projection_id=p.projection_id AND t.axis='LIFECYCLE'
                    WHERE t.tag_code=? AND p.knowledge_id IN ("""+','.join('?' for _ in ids)+')',
                [lifecycle_code.upper(),*ids],
            ).fetchall()
        return [row[0] for row in rows]
