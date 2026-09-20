"""SQLite repository for the independent REQ-022 knowledge store."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator
import hashlib
import json
import os
import shutil
import sqlite3
import uuid

from quality_knowledge.sqlite_tuning import configure_connection
from .document_parser import PARSER_VERSION, ParseResult


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _row(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row else None


class MajorKnowledgeRepository:
    SCHEMA_VERSION = 1

    def __init__(self, db_path: str | Path, attachment_root: str | Path):
        self.db_path = Path(db_path)
        self.attachment_root = Path(attachment_root)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.attachment_root.mkdir(parents=True, exist_ok=True)
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(schema)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return configure_connection(connection)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def schema_version(self) -> int:
        with self.connect() as connection:
            return int(connection.execute("SELECT MAX(version) FROM kb_schema_version").fetchone()[0] or 0)

    def create_case(self, title: str, group_code: str, domain: str = "", case_type: str = "MAJOR_REVIEW", legacy_case_id: str = "") -> dict:
        if not title.strip() or not group_code.strip():
            raise ValueError("CASE_TITLE_AND_GROUP_REQUIRED")
        case_id = _id("KCASE")
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO kb_case(case_id,case_type,title,domain,group_code,legacy_case_id) VALUES(?,?,?,?,?,?)",
                (case_id, case_type, title.strip(), domain.strip(), group_code.strip(), legacy_case_id.strip()),
            )
        return self.get_case(case_id) or {}

    def get_case(self, case_id: str) -> dict | None:
        with self.connect() as connection:
            return _row(connection.execute("SELECT * FROM kb_case WHERE case_id=?", (case_id,)).fetchone())

    def update_case_status(self, case_id: str, status: str) -> None:
        with self.connect() as connection:
            updated = connection.execute(
                "UPDATE kb_case SET status=?,updated_at=CURRENT_TIMESTAMP WHERE case_id=?", (status, case_id)
            ).rowcount
        if not updated:
            raise KeyError(case_id)

    def list_cases(self, *, group_code: str = "", status: str = "", tag: str = "", page: int = 1, page_size: int = 20) -> dict:
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 200))
        where = ["c.archived_at IS NULL"]
        params: list = []
        if group_code:
            where.append("c.group_code=?")
            params.append(group_code)
        if status:
            where.append("c.status=?")
            params.append(status)
        if tag:
            where.append("EXISTS(SELECT 1 FROM kb_tag_link tl JOIN kb_tag t ON t.tag_id=tl.tag_id WHERE tl.target_type='CASE' AND tl.target_id=c.case_id AND tl.state='ACTIVE' AND t.code=?)")
            params.append(tag)
        clause = " AND ".join(where)
        with self.connect() as connection:
            total = int(connection.execute(f"SELECT COUNT(*) FROM kb_case c WHERE {clause}", params).fetchone()[0])
            rows = connection.execute(
                f"""SELECT c.*,
                    (SELECT COUNT(*) FROM kb_case_document cd WHERE cd.case_id=c.case_id) document_count,
                    (SELECT COUNT(*) FROM kb_event e WHERE e.case_id=c.case_id) event_count,
                    (SELECT COUNT(*) FROM kb_source_link s WHERE s.case_id=c.case_id AND s.match_status IN ('PENDING','CONFLICT','NOT_FOUND')) pending_link_count,
                    (SELECT COUNT(*) FROM kb_entry e WHERE e.case_id=c.case_id AND e.status IN ('PENDING','MISSING')) pending_entry_count
                  FROM kb_case c WHERE {clause}
                  ORDER BY c.updated_at DESC,c.case_id LIMIT ? OFFSET ?""",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        return {"page": page, "page_size": page_size, "total": total, "items": [dict(row) for row in rows]}

    def ingest_file(
        self,
        case_id: str,
        source_path: str | Path,
        *,
        role: str = "PRIMARY",
        document_id: str | None = None,
        logical_name: str | None = None,
    ) -> dict:
        case = self.get_case(case_id)
        if not case:
            raise KeyError(case_id)
        source = Path(source_path)
        suffix = source.suffix.lower()
        if suffix not in {".pdf", ".docx", ".doc"}:
            raise ValueError("UNSUPPORTED_DOCUMENT_TYPE")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        with self.transaction() as connection:
            existing = connection.execute(
                """SELECT v.*,d.group_code FROM kb_document_version v JOIN kb_document d ON d.document_id=v.document_id
                   WHERE d.group_code=? AND v.content_hash=? ORDER BY v.created_at LIMIT 1""",
                (case["group_code"], digest),
            ).fetchone()
            if existing:
                connection.execute(
                    "INSERT OR IGNORE INTO kb_case_document(case_id,version_id,document_role) VALUES(?,?,?)",
                    (case_id, existing["version_id"], role),
                )
                return {"action": "SKIPPED", **dict(existing)}
            if document_id:
                document = connection.execute(
                    "SELECT * FROM kb_document WHERE document_id=? AND group_code=? AND archived_at IS NULL",
                    (document_id, case["group_code"]),
                ).fetchone()
                if not document:
                    raise ValueError("DOCUMENT_NOT_FOUND_OR_WRONG_GROUP")
            else:
                document_id = _id("KDOC")
                connection.execute(
                    "INSERT INTO kb_document(document_id,group_code,logical_name) VALUES(?,?,?)",
                    (document_id, case["group_code"], (logical_name or source.stem).strip()),
                )
            version_no = int(connection.execute(
                "SELECT COALESCE(MAX(version_no),0)+1 FROM kb_document_version WHERE document_id=?", (document_id,)
            ).fetchone()[0])
            version_id = _id("KVER")
            group_dir = self.attachment_root / case["group_code"]
            group_dir.mkdir(parents=True, exist_ok=True)
            target = group_dir / f"{digest}{suffix}"
            if not target.exists():
                temp = target.with_suffix(target.suffix + f".{uuid.uuid4().hex}.tmp")
                shutil.copyfile(source, temp)
                os.replace(temp, target)
            relative = target.relative_to(self.attachment_root).as_posix()
            connection.execute(
                """INSERT INTO kb_document_version(
                     version_id,document_id,version_no,content_hash,original_filename,media_type,attachment_path,size_bytes)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (version_id, document_id, version_no, digest, source.name, suffix.lstrip(".").upper(), relative, source.stat().st_size),
            )
            connection.execute(
                "INSERT INTO kb_case_document(case_id,version_id,document_role) VALUES(?,?,?)", (case_id, version_id, role)
            )
        return {"action": "NEW" if version_no == 1 else "UPDATED", "version_id": version_id, "document_id": document_id, "version_no": version_no, "content_hash": digest, "attachment_path": relative}

    def version(self, version_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT v.*,d.group_code,d.logical_name FROM kb_document_version v
                   JOIN kb_document d ON d.document_id=v.document_id WHERE v.version_id=?""", (version_id,)
            ).fetchone()
            return _row(row)

    def attachment_path(self, version_id: str) -> Path:
        version = self.version(version_id)
        if not version:
            raise KeyError(version_id)
        path = (self.attachment_root / version["attachment_path"]).resolve()
        root = self.attachment_root.resolve()
        if root not in path.parents:
            raise ValueError("ATTACHMENT_PATH_OUTSIDE_ROOT")
        return path

    def save_parse_result(self, version_id: str, result: ParseResult) -> list[dict]:
        with self.transaction() as connection:
            version = connection.execute("SELECT 1 FROM kb_document_version WHERE version_id=?", (version_id,)).fetchone()
            if not version:
                raise KeyError(version_id)
            connection.execute("DELETE FROM kb_fragment WHERE version_id=?", (version_id,))
            ids = []
            for fragment in result.fragments:
                fragment_id = _id("KFRAG")
                connection.execute(
                    """INSERT INTO kb_fragment(fragment_id,version_id,ordinal,section_path,location_type,location_ref,fragment_type,text_content,text_hash)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (fragment_id, version_id, fragment.ordinal, fragment.section_path, fragment.location_type, fragment.location_ref, fragment.fragment_type, fragment.text, fragment.text_hash),
                )
                ids.append({"fragment_id": fragment_id, "ordinal": fragment.ordinal, "location_ref": fragment.location_ref})
            status = "FAILED" if not result.fragments else ("WARNING" if result.warnings else "SUCCESS")
            connection.execute(
                "UPDATE kb_document_version SET parser_version=?,parse_status=?,parse_warnings_json=? WHERE version_id=?",
                (PARSER_VERSION, status, _json(result.warnings), version_id),
            )
        return ids

    def fragments(self, version_id: str) -> list[dict]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM kb_fragment WHERE version_id=? ORDER BY ordinal", (version_id,)
            )]

    def upsert_event(self, case_id: str, *, standard_itr: str = "", internal_event_key: str = "", title: str = "") -> dict:
        case = self.get_case(case_id)
        if not case:
            raise KeyError(case_id)
        key = internal_event_key.strip() or standard_itr.strip() or _id("INTERNAL")
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM kb_event WHERE case_id=? AND internal_event_key=?", (case_id, key)
            ).fetchone()
            if existing:
                return dict(existing)
            event_id = _id("KEVT")
            connection.execute(
                "INSERT INTO kb_event(event_id,case_id,standard_itr,internal_event_key,event_title,group_code) VALUES(?,?,?,?,?,?)",
                (event_id, case_id, standard_itr, key, title, case["group_code"]),
            )
        return self.event(event_id) or {}

    def event(self, event_id: str) -> dict | None:
        with self.connect() as connection:
            return _row(connection.execute("SELECT * FROM kb_event WHERE event_id=?", (event_id,)).fetchone())

    def events(self, case_id: str) -> list[dict]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM kb_event WHERE case_id=? ORDER BY created_at,event_id", (case_id,))]

    def add_source_link(self, case_id: str, event_id: str | None, source: dict, *, standard_itr: str, role: str, status: str) -> dict:
        link_id = _id("KSRC")
        record_id = str(source.get("record_id") or f"UNRESOLVED:{standard_itr}")
        source_type = str(source.get("source_type") or "ITR")
        source_system = str(source.get("source_system") or "BUSINESS_DB")
        source_group = str(source.get("group_code") or self.get_case(case_id)["group_code"])
        source_version = str(source.get("issue_version_id") or source.get("version_no") or source.get("source_hash") or "")
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO kb_source_link(source_link_id,case_id,event_id,source_system,source_type,record_id,
                     source_group,source_version,standard_itr,relation_role,match_status,snapshot_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(case_id,source_system,source_type,record_id,relation_role) DO UPDATE SET
                     event_id=excluded.event_id,source_version=excluded.source_version,standard_itr=excluded.standard_itr,
                     match_status=excluded.match_status,snapshot_json=excluded.snapshot_json,checked_at=CURRENT_TIMESTAMP""",
                (link_id, case_id, event_id, source_system, source_type, record_id, source_group, source_version, standard_itr, role, status, _json(source)),
            )
            row = connection.execute(
                "SELECT * FROM kb_source_link WHERE case_id=? AND source_system=? AND source_type=? AND record_id=? AND relation_role=?",
                (case_id, source_system, source_type, record_id, role),
            ).fetchone()
        return dict(row)

    def source_links(self, case_id: str) -> list[dict]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM kb_source_link WHERE case_id=? ORDER BY relation_role,standard_itr,record_id", (case_id,))]

    def reconcile_sources(self, case_id: str, live_records: dict[str, list[dict]]) -> dict:
        updated = {"stale": 0, "unavailable": 0, "current": 0}
        with self.transaction() as connection:
            rows = connection.execute("SELECT * FROM kb_source_link WHERE case_id=? AND relation_role='CURRENT_EVENT'", (case_id,)).fetchall()
            for row in rows:
                live = next((item for item in live_records.get(row["standard_itr"], []) if str(item.get("record_id")) == row["record_id"]), None)
                if live is None:
                    status = "SOURCE_UNAVAILABLE"
                    updated["unavailable"] += 1
                else:
                    version = str(live.get("issue_version_id") or live.get("version_no") or live.get("source_hash") or "")
                    status = "STALE" if row["source_version"] and version and version != row["source_version"] else "LINKED"
                    updated["stale" if status == "STALE" else "current"] += 1
                connection.execute("UPDATE kb_source_link SET match_status=?,checked_at=CURRENT_TIMESTAMP WHERE source_link_id=?", (status, row["source_link_id"]))
        return updated

    def seed_skill(self, config: dict) -> dict:
        normalized = _json(config)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM kb_skill_version WHERE skill_code=? AND config_hash=?", (config["skill_code"], digest)).fetchone()
            if row:
                return dict(row)
            version = int(connection.execute("SELECT COALESCE(MAX(version),0)+1 FROM kb_skill_version WHERE skill_code=?", (config["skill_code"],)).fetchone()[0])
            skill_id = _id("KSKILL")
            connection.execute(
                """INSERT INTO kb_skill_version(skill_version_id,skill_code,version,material_types_json,
                   required_sections_json,auxiliary_sections_json,output_schema_json,prompt_rules,tag_dictionary_json,
                   input_budget,output_budget,config_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (skill_id, config["skill_code"], version, _json(config["material_types"]), _json(config["required_sections"]), _json(config.get("auxiliary_sections", [])), _json(config["output_schema"]), config["prompt_rules"], _json(config.get("tag_dictionary", {})), int(config["input_budget"]), int(config["output_budget"]), digest),
            )
        return self.skill(skill_id) or {}

    def skill(self, skill_version_id: str) -> dict | None:
        with self.connect() as connection:
            return _row(connection.execute("SELECT * FROM kb_skill_version WHERE skill_version_id=?", (skill_version_id,)).fetchone())

    def active_skill(self, skill_code: str) -> dict | None:
        with self.connect() as connection:
            return _row(connection.execute("SELECT * FROM kb_skill_version WHERE skill_code=? AND enabled=1 ORDER BY version DESC LIMIT 1", (skill_code,)).fetchone())

    def list_skills(self) -> list[dict]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM kb_skill_version ORDER BY skill_code,version DESC")]

    def create_or_reuse_run(self, case_id: str, version_id: str | None, run_type: str, input_hash: str, *, skill_version_id: str | None = None, model_profile: str = "") -> tuple[dict, bool]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM kb_run WHERE case_id=? AND run_type=? AND input_hash=?", (case_id, run_type, input_hash)).fetchone()
            if row:
                return dict(row), True
            run_id = _id("KRUN")
            connection.execute(
                "INSERT INTO kb_run(run_id,case_id,version_id,run_type,skill_version_id,model_profile,input_hash,state) VALUES(?,?,?,?,?,?,?,'QUEUED')",
                (run_id, case_id, version_id, run_type, skill_version_id, model_profile, input_hash),
            )
            row = connection.execute("SELECT * FROM kb_run WHERE run_id=?", (run_id,)).fetchone()
        return dict(row), False

    def set_run(self, run_id: str, state: str, *, coverage_total: int | None = None, coverage_processed: int | None = None, error_code: str = "", error_detail: str = "") -> None:
        parts = ["state=?", "error_code=?", "error_detail=?"]
        params: list = [state, error_code, error_detail]
        if state == "RUNNING":
            parts.append("started_at=COALESCE(started_at,CURRENT_TIMESTAMP)")
        if state in {"COMPLETED", "FAILED", "PARTIAL", "CANCELLED"}:
            parts.append("completed_at=CURRENT_TIMESTAMP")
        if coverage_total is not None:
            parts.append("coverage_total=?")
            params.append(coverage_total)
        if coverage_processed is not None:
            parts.append("coverage_processed=?")
            params.append(coverage_processed)
        params.append(run_id)
        with self.connect() as connection:
            connection.execute(f"UPDATE kb_run SET {','.join(parts)} WHERE run_id=?", params)

    def cancel_run(self, run_id: str) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM kb_run WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError(run_id)
            if row["state"] in {"COMPLETED", "FAILED"}:
                raise ValueError("TERMINAL_RUN_CANNOT_BE_CANCELLED")
            connection.execute(
                "UPDATE kb_run SET state='CANCELLED',completed_at=CURRENT_TIMESTAMP,error_code='USER_CANCELLED' WHERE run_id=?",
                (run_id,),
            )
        return self.run(run_id) or {}

    def save_step(self, run_id: str, step_code: str, idempotency_key: str, state: str, *, input_value=None, result=None, error_detail: str = "") -> dict:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO kb_step(step_id,run_id,step_code,idempotency_key,state,attempt_count,input_json,result_json,error_detail,started_at,completed_at)
                   VALUES(?,?,?,?,?,1,?,?,?,CURRENT_TIMESTAMP,CASE WHEN ? IN ('SUCCESS','FAILED','SKIPPED') THEN CURRENT_TIMESTAMP END)
                   ON CONFLICT(run_id,step_code) DO UPDATE SET state=excluded.state,attempt_count=kb_step.attempt_count+1,
                   result_json=excluded.result_json,error_detail=excluded.error_detail,completed_at=excluded.completed_at""",
                (_id("KSTEP"), run_id, step_code, idempotency_key, state, _json(input_value or {}), _json(result or {}), error_detail, state),
            )
            return dict(connection.execute("SELECT * FROM kb_step WHERE run_id=? AND step_code=?", (run_id, step_code)).fetchone())

    def add_entry(self, case_id: str, entry_type: str, content: str, *, assertion_kind: str, origin: str, status: str, event_id: str | None = None, applicability: str = "", limitations: str = "", model_profile: str = "", skill_version_id: str | None = None, evidence: Iterable[dict] = ()) -> dict:
        entry_id, revision_id = _id("KENTRY"), _id("KREV")
        with self.transaction() as connection:
            connection.execute("INSERT INTO kb_entry(entry_id,case_id,event_id,entry_type,status) VALUES(?,?,?,?,?)", (entry_id, case_id, event_id, entry_type, status))
            connection.execute(
                """INSERT INTO kb_entry_revision(revision_id,entry_id,revision_no,content,applicability,limitations,assertion_kind,origin,model_profile,skill_version_id)
                   VALUES(?,?,1,?,?,?,?,?,?,?)""",
                (revision_id, entry_id, content, applicability, limitations, assertion_kind, origin, model_profile, skill_version_id),
            )
            connection.execute("UPDATE kb_entry SET current_revision_id=? WHERE entry_id=?", (revision_id, entry_id))
            for item in evidence:
                connection.execute(
                    "INSERT INTO kb_evidence(evidence_id,revision_id,fragment_id,source_link_id,locator,excerpt) VALUES(?,?,?,?,?,?)",
                    (_id("KEV"), revision_id, item.get("fragment_id"), item.get("source_link_id"), item.get("locator", ""), item.get("excerpt", "")),
                )
        return self.entry(entry_id) or {}

    def entry(self, entry_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT e.*,r.revision_no,r.content,r.applicability,r.limitations,r.assertion_kind,r.origin,r.model_profile
                   FROM kb_entry e JOIN kb_entry_revision r ON r.revision_id=e.current_revision_id WHERE e.entry_id=?""", (entry_id,)
            ).fetchone()
            item = _row(row)
            if item:
                item["evidence"] = [dict(ev) for ev in connection.execute(
                    "SELECT * FROM kb_evidence WHERE revision_id=? ORDER BY evidence_id", (item["current_revision_id"],)
                )]
            return item

    def revise_entry(self, entry_id: str, content: str, status: str, reviewer: str, reason: str = "") -> dict:
        before = self.entry(entry_id)
        if not before:
            raise KeyError(entry_id)
        with self.transaction() as connection:
            revision_no = int(connection.execute("SELECT MAX(revision_no)+1 FROM kb_entry_revision WHERE entry_id=?", (entry_id,)).fetchone()[0])
            revision_id = _id("KREV")
            connection.execute(
                """INSERT INTO kb_entry_revision(revision_id,entry_id,revision_no,content,applicability,limitations,assertion_kind,origin,created_by)
                   VALUES(?,?,?,?,?,?, 'HUMAN_REVISION','HUMAN',?)""",
                (revision_id, entry_id, revision_no, content, before["applicability"], before["limitations"], reviewer),
            )
            for evidence in before.get("evidence", []):
                connection.execute(
                    "INSERT INTO kb_evidence(evidence_id,revision_id,fragment_id,source_link_id,locator,excerpt) VALUES(?,?,?,?,?,?)",
                    (_id("KEV"), revision_id, evidence.get("fragment_id"), evidence.get("source_link_id"), evidence.get("locator", ""), evidence.get("excerpt", "")),
                )
            connection.execute("UPDATE kb_entry SET current_revision_id=?,status=? WHERE entry_id=?", (revision_id, status, entry_id))
            connection.execute(
                "INSERT INTO kb_review(review_id,target_type,target_id,action,before_json,after_json,reason,reviewer) VALUES(?,?,?,?,?,?,?,?)",
                (_id("KREVIEW"), "ENTRY", entry_id, status, _json(before), _json({"content": content, "status": status}), reason, reviewer),
            )
        return self.entry(entry_id) or {}

    def entries(self, case_id: str) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT e.*,r.revision_no,r.content,r.applicability,r.limitations,r.assertion_kind,r.origin,r.model_profile
                   FROM kb_entry e JOIN kb_entry_revision r ON r.revision_id=e.current_revision_id
                   WHERE e.case_id=? AND e.archived_at IS NULL ORDER BY e.entry_type,e.created_at""", (case_id,)
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["evidence"] = [dict(ev) for ev in connection.execute(
                    "SELECT * FROM kb_evidence WHERE revision_id=? ORDER BY evidence_id", (item["current_revision_id"],)
                )]
                result.append(item)
            return result

    def clear_pending_ai_entries(self, case_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE kb_entry SET archived_at=CURRENT_TIMESTAMP WHERE case_id=? AND status IN ('PENDING','MISSING') AND current_revision_id IN (SELECT revision_id FROM kb_entry_revision WHERE origin='AI')",
                (case_id,),
            )

    def add_tag(self, namespace: str, code: str, label: str, target_type: str, target_id: str, *, state: str = "ACTIVE", derived_from: str = "") -> None:
        with self.transaction() as connection:
            row = connection.execute("SELECT tag_id FROM kb_tag WHERE namespace=? AND code=?", (namespace, code)).fetchone()
            tag_id = row[0] if row else _id("KTAG")
            if not row:
                connection.execute("INSERT INTO kb_tag(tag_id,namespace,code,label) VALUES(?,?,?,?)", (tag_id, namespace, code, label))
            connection.execute(
                """INSERT INTO kb_tag_link(tag_link_id,tag_id,target_type,target_id,state,derived_from) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(tag_id,target_type,target_id) DO UPDATE SET state=excluded.state,derived_from=excluded.derived_from""",
                (_id("KTL"), tag_id, target_type, target_id, state, derived_from),
            )

    def case_detail(self, case_id: str) -> dict | None:
        case = self.get_case(case_id)
        if not case:
            return None
        with self.connect() as connection:
            case["documents"] = [dict(row) for row in connection.execute(
                """SELECT cd.document_role,v.*,d.logical_name FROM kb_case_document cd
                   JOIN kb_document_version v ON v.version_id=cd.version_id JOIN kb_document d ON d.document_id=v.document_id
                   WHERE cd.case_id=? ORDER BY d.created_at,v.version_no""", (case_id,)
            )]
            case["events"] = [dict(row) for row in connection.execute("SELECT * FROM kb_event WHERE case_id=? ORDER BY created_at", (case_id,))]
            case["source_links"] = [dict(row) for row in connection.execute("SELECT * FROM kb_source_link WHERE case_id=? ORDER BY relation_role,standard_itr", (case_id,))]
            case["tags"] = [dict(row) for row in connection.execute(
                """SELECT t.namespace,t.code,t.label,tl.state FROM kb_tag_link tl JOIN kb_tag t ON t.tag_id=tl.tag_id
                   WHERE tl.target_type='CASE' AND tl.target_id=? ORDER BY t.namespace,t.code""", (case_id,)
            )]
            case["runs"] = [dict(row) for row in connection.execute("SELECT * FROM kb_run WHERE case_id=? ORDER BY created_at DESC", (case_id,))]
        case["entries"] = self.entries(case_id)
        return case

    def add_repeat_result(self, run_id: str, current_event_id: str, decision: str, confidence: float, *, candidate_event_id: str | None = None, candidate_case_id: str | None = None, legacy_result=None, report=None, markdown: str = "") -> dict:
        result_id = _id("KREP")
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO kb_repeat_result(repeat_result_id,run_id,current_event_id,candidate_event_id,candidate_case_id,
                   decision,confidence,legacy_result_json,report_json,report_markdown) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (result_id, run_id, current_event_id, candidate_event_id, candidate_case_id, decision, confidence, _json(legacy_result or {}), _json(report or {}), markdown),
            )
            row = connection.execute("SELECT * FROM kb_repeat_result WHERE repeat_result_id=?", (result_id,)).fetchone()
        return dict(row)

    def confirm_repeat(self, result_id: str, action: str, reviewer: str, reason: str = "") -> dict:
        if action not in {"CONFIRMED_REPEAT", "RELATED", "NOT_REPEAT", "INSUFFICIENT_EVIDENCE"}:
            raise ValueError("INVALID_REPEAT_REVIEW")
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM kb_repeat_result WHERE repeat_result_id=?", (result_id,)).fetchone()
            if not row:
                raise KeyError(result_id)
            connection.execute("UPDATE kb_repeat_result SET review_status=? WHERE repeat_result_id=?", (action, result_id))
            connection.execute(
                "INSERT INTO kb_review(review_id,target_type,target_id,action,before_json,after_json,reason,reviewer) VALUES(?,?,?,?,?,?,?,?)",
                (_id("KREVIEW"), "REPEAT", result_id, action, _json(dict(row)), _json({"review_status": action}), reason, reviewer),
            )
            if action == "CONFIRMED_REPEAT":
                tag_id = _id("KTAG")
                connection.execute("INSERT OR IGNORE INTO kb_tag(tag_id,namespace,code,label) VALUES(?,?,?,?)", (tag_id, "RELATION", "REPEAT_CONFIRMED", "重复问题（人工确认）"))
                actual = connection.execute("SELECT tag_id FROM kb_tag WHERE namespace='RELATION' AND code='REPEAT_CONFIRMED'").fetchone()[0]
                connection.execute("INSERT OR IGNORE INTO kb_tag_link(tag_link_id,tag_id,target_type,target_id,state,derived_from) VALUES(?,?,?,?,?,?)", (_id("KTL"), actual, "EVENT", row["current_event_id"], "ACTIVE", result_id))
            updated = connection.execute("SELECT * FROM kb_repeat_result WHERE repeat_result_id=?", (result_id,)).fetchone()
        return dict(updated)

    def run(self, run_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM kb_run WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["steps"] = [dict(step) for step in connection.execute("SELECT * FROM kb_step WHERE run_id=? ORDER BY started_at,step_id", (run_id,))]
            result["repeat_results"] = [dict(item) for item in connection.execute("SELECT * FROM kb_repeat_result WHERE run_id=?", (run_id,))]
            return result

    def list_runs(self, state: str = "", limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM kb_run"
        params: list = []
        if state:
            sql += " WHERE state=?"
            params.append(state)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(min(max(int(limit), 1), 500))
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, params)]
