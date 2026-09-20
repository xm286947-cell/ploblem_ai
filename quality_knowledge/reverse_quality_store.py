"""Persistence contract for reverse-quality V0.1 results.

The service layer depends on ReverseQualityRepository rather than SQLite details so
future PostgreSQL migration does not require rewriting reverse-quality business logic.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from quality_knowledge.sqlite_tuning import configure_connection


RESULT_VERSION = "reverse-quality-v0.1"


SCHEMA = """
CREATE TABLE IF NOT EXISTS reverse_quality_analysis(
 analysis_id TEXT PRIMARY KEY,
 canonical_itr TEXT NOT NULL UNIQUE,
 product_code TEXT NOT NULL,
 taxonomy_version_id TEXT,
 latest_valid_run_id TEXT,
 status TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS reverse_quality_run(
 run_id TEXT PRIMARY KEY,
 analysis_id TEXT NOT NULL REFERENCES reverse_quality_analysis(analysis_id),
 run_seq INTEGER NOT NULL,
 product_code TEXT NOT NULL DEFAULT '',
 taxonomy_version_id TEXT,
 source_hash TEXT NOT NULL,
 status TEXT NOT NULL,
 input_json TEXT NOT NULL DEFAULT '{}',
 model TEXT,
 error TEXT NOT NULL DEFAULT '',
 reused_from_run_id TEXT,
 started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 completed_at TEXT,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(analysis_id, run_seq)
);
CREATE TABLE IF NOT EXISTS reverse_quality_field_result(
 run_id TEXT NOT NULL REFERENCES reverse_quality_run(run_id),
 field_name TEXT NOT NULL,
 result_json TEXT NOT NULL,
 PRIMARY KEY(run_id, field_name)
);
CREATE TABLE IF NOT EXISTS reverse_quality_evidence(
 run_id TEXT NOT NULL REFERENCES reverse_quality_run(run_id),
 evidence_id TEXT NOT NULL,
 evidence_json TEXT NOT NULL,
 PRIMARY KEY(run_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS reverse_quality_missing_information(
 missing_id TEXT PRIMARY KEY,
 analysis_id TEXT NOT NULL REFERENCES reverse_quality_analysis(analysis_id),
 run_id TEXT NOT NULL REFERENCES reverse_quality_run(run_id),
 field_name TEXT,
 reason TEXT,
 question TEXT,
 evidence_needed TEXT,
 status TEXT NOT NULL DEFAULT 'PENDING',
 answer TEXT,
 reviewer TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS reverse_quality_human_review(
 review_id INTEGER PRIMARY KEY AUTOINCREMENT,
 analysis_id TEXT NOT NULL REFERENCES reverse_quality_analysis(analysis_id),
 run_id TEXT NOT NULL REFERENCES reverse_quality_run(run_id),
 target_type TEXT NOT NULL,
 field_name TEXT,
 action TEXT NOT NULL,
 old_json TEXT NOT NULL,
 new_json TEXT NOT NULL,
 reviewer TEXT NOT NULL,
 reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS reverse_quality_scene_match(
 match_id INTEGER PRIMARY KEY AUTOINCREMENT,
 analysis_id TEXT NOT NULL REFERENCES reverse_quality_analysis(analysis_id),
 run_id TEXT NOT NULL REFERENCES reverse_quality_run(run_id),
 status TEXT NOT NULL,
 matched_scene_id TEXT,
 match_reason TEXT,
 missing_condition TEXT,
 reviewed INTEGER NOT NULL DEFAULT 0,
 reviewer TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_rq_analysis_canonical ON reverse_quality_analysis(canonical_itr);
CREATE INDEX IF NOT EXISTS idx_rq_analysis_latest_run ON reverse_quality_analysis(latest_valid_run_id);
CREATE INDEX IF NOT EXISTS idx_rq_run_analysis_seq ON reverse_quality_run(analysis_id, run_seq DESC);
CREATE INDEX IF NOT EXISTS idx_rq_run_status_updated ON reverse_quality_run(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_rq_run_source_hash ON reverse_quality_run(source_hash);
CREATE INDEX IF NOT EXISTS idx_rq_missing_analysis_status ON reverse_quality_missing_information(analysis_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_rq_review_run_target ON reverse_quality_human_review(run_id, target_type, field_name, review_id);
CREATE INDEX IF NOT EXISTS idx_rq_scene_run ON reverse_quality_scene_match(run_id, match_id DESC);
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return copy.deepcopy(default)


@dataclass(frozen=True)
class ReverseQualityResult:
    result_version: str
    analysis_id: str
    run_id: str
    status: str
    identity: dict[str, str]
    fields: dict[str, dict[str, Any]]
    missing_information: list[dict[str, Any]]
    scene_match: dict[str, Any]
    model: str = ""
    run_seq: int = 0
    started_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return _json(self.to_dict())


class ReverseQualityRepository(ABC):
    @abstractmethod
    def start_run(self, *, canonical_itr: str, product_code: str, taxonomy_version_id: str,
                  source_hash: str, input_payload: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def complete_run(self, run_id: str, *, fields: dict[str, Any], evidence: dict[str, Any],
                     scene_match: dict[str, Any], model: str, input_payload: dict[str, Any],
                     missing_information: list[dict[str, Any]] | None = None) -> None: ...

    @abstractmethod
    def fail_run(self, run_id: str, error: str) -> None: ...

    @abstractmethod
    def get_latest(self, canonical_itr: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def reuse_run(self, run_id: str, source_run_id: str) -> None: ...

    @abstractmethod
    def save_field_review(self, canonical_itr: str, *, field_name: str, action: str,
                          old: dict[str, Any], new: dict[str, Any], reviewer: str,
                          analysis_status: str) -> None: ...

    @abstractmethod
    def save_scene_review(self, canonical_itr: str, *, scene_match: dict[str, Any], reviewer: str,
                          analysis_status: str) -> None: ...

    @abstractmethod
    def resolve_missing_information(self, canonical_itr: str, *, missing_id: str, status: str,
                                    answer: str, reviewer: str) -> dict[str, Any]: ...


class SQLiteReverseQualityRepository(ReverseQualityRepository):
    """SQLite implementation of the reverse-quality repository contract."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return configure_connection(connection)

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(SCHEMA)
            run_columns = {row["name"] for row in connection.execute("PRAGMA table_info(reverse_quality_run)")}
            if "product_code" not in run_columns:
                connection.execute("ALTER TABLE reverse_quality_run ADD COLUMN product_code TEXT NOT NULL DEFAULT ''")
            if "taxonomy_version_id" not in run_columns:
                connection.execute("ALTER TABLE reverse_quality_run ADD COLUMN taxonomy_version_id TEXT")
            connection.execute(
                """UPDATE reverse_quality_run
                   SET product_code=COALESCE(NULLIF(product_code,''),(
                         SELECT product_code FROM reverse_quality_analysis
                         WHERE analysis_id=reverse_quality_run.analysis_id)),
                       taxonomy_version_id=COALESCE(taxonomy_version_id,(
                         SELECT taxonomy_version_id FROM reverse_quality_analysis
                         WHERE analysis_id=reverse_quality_run.analysis_id))
                   WHERE COALESCE(product_code,'')='' OR taxonomy_version_id IS NULL"""
            )

    @contextmanager
    def _transaction(self):
        connection = self.connect()
        begun = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            begun = True
            yield connection
            connection.execute("COMMIT")
            begun = False
        except Exception:
            if begun:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def start_run(self, *, canonical_itr: str, product_code: str, taxonomy_version_id: str,
                  source_hash: str, input_payload: dict[str, Any]) -> dict[str, Any]:
        analysis_id = "RQA-" + uuid.uuid4().hex
        run_id = "RQRUN-" + uuid.uuid4().hex
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO reverse_quality_analysis(
                       analysis_id,canonical_itr,product_code,taxonomy_version_id)
                   VALUES(?,?,?,?)
                   ON CONFLICT(canonical_itr) DO NOTHING""",
                (analysis_id, canonical_itr, product_code, taxonomy_version_id),
            )
            row = connection.execute(
                "SELECT analysis_id FROM reverse_quality_analysis WHERE canonical_itr=?",
                (canonical_itr,),
            ).fetchone()
            analysis_id = row["analysis_id"]
            run_seq = connection.execute(
                "SELECT COALESCE(MAX(run_seq),0)+1 value FROM reverse_quality_run WHERE analysis_id=?",
                (analysis_id,),
            ).fetchone()["value"]
            connection.execute(
                """INSERT INTO reverse_quality_run(
                       run_id,analysis_id,run_seq,product_code,taxonomy_version_id,source_hash,status,input_json)
                   VALUES(?,?,?,?,?,?, 'RUNNING', ?)""",
                (run_id, analysis_id, run_seq, product_code, taxonomy_version_id,
                 source_hash, _json(input_payload)),
            )
        return {"analysis_id": analysis_id, "run_id": run_id, "run_seq": int(run_seq)}

    @staticmethod
    def _can_promote(connection: sqlite3.Connection, analysis_id: str, run_seq: int) -> bool:
        current = connection.execute(
            """SELECT current_run.run_seq
               FROM reverse_quality_analysis analysis
               LEFT JOIN reverse_quality_run current_run
                 ON current_run.run_id=analysis.latest_valid_run_id
               WHERE analysis.analysis_id=?""",
            (analysis_id,),
        ).fetchone()
        return current is None or current["run_seq"] is None or int(current["run_seq"]) <= int(run_seq)

    def complete_run(self, run_id: str, *, fields: dict[str, Any], evidence: dict[str, Any],
                     scene_match: dict[str, Any], model: str, input_payload: dict[str, Any],
                     missing_information: list[dict[str, Any]] | None = None) -> None:
        missing_information = missing_information or []
        with self._transaction() as connection:
            run = connection.execute(
                "SELECT * FROM reverse_quality_run WHERE run_id=?", (run_id,)
            ).fetchone()
            if not run:
                raise KeyError(run_id)
            if run["status"] != "RUNNING":
                raise ValueError("RUN_NOT_RUNNING")
            for field_name, value in fields.items():
                connection.execute(
                    "INSERT INTO reverse_quality_field_result(run_id,field_name,result_json) VALUES(?,?,?)",
                    (run_id, field_name, _json(value)),
                )
            for evidence_id, value in evidence.items():
                connection.execute(
                    "INSERT INTO reverse_quality_evidence(run_id,evidence_id,evidence_json) VALUES(?,?,?)",
                    (run_id, evidence_id, _json(value)),
                )
            for item in missing_information:
                status = str(item.get("status") or "PENDING")
                if status not in {"PENDING", "CONFIRMED", "NOT_APPLICABLE"}:
                    status = "PENDING"
                connection.execute(
                    """INSERT INTO reverse_quality_missing_information(
                           missing_id,analysis_id,run_id,field_name,reason,question,evidence_needed,
                           status,answer,reviewer)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        str(item.get("missing_id") or "RQM-" + uuid.uuid4().hex),
                        run["analysis_id"], run_id, str(item.get("field_name") or ""),
                        str(item.get("reason") or ""), str(item.get("question") or ""),
                        _json(item.get("evidence_needed") or []), status,
                        str(item.get("answer") or ""), str(item.get("reviewer") or ""),
                    ),
                )
            connection.execute(
                """INSERT INTO reverse_quality_scene_match(
                       analysis_id,run_id,status,matched_scene_id,match_reason,missing_condition,reviewed,reviewer)
                   VALUES(?,?,?,?,?,?,0,'')""",
                (
                    run["analysis_id"], run_id, str(scene_match.get("status") or "NEED_REVIEW"),
                    str(scene_match.get("matched_scene_id") or ""),
                    str(scene_match.get("match_reason") or ""),
                    str(scene_match.get("missing_condition") or ""),
                ),
            )
            connection.execute(
                """UPDATE reverse_quality_run
                   SET status='COMPLETED',input_json=?,model=?,error='',completed_at=CURRENT_TIMESTAMP,
                       updated_at=CURRENT_TIMESTAMP
                   WHERE run_id=?""",
                (_json(input_payload), model, run_id),
            )
            if self._can_promote(connection, run["analysis_id"], run["run_seq"]):
                connection.execute(
                    """UPDATE reverse_quality_analysis
                       SET latest_valid_run_id=?,product_code=?,taxonomy_version_id=?,
                           status='PENDING_REVIEW',updated_at=CURRENT_TIMESTAMP
                       WHERE analysis_id=?""",
                    (run_id, run["product_code"], run["taxonomy_version_id"], run["analysis_id"]),
                )

    def reuse_run(self, run_id: str, source_run_id: str) -> None:
        with self._transaction() as connection:
            run = connection.execute("SELECT * FROM reverse_quality_run WHERE run_id=?", (run_id,)).fetchone()
            source = connection.execute(
                "SELECT * FROM reverse_quality_run WHERE run_id=? AND status='COMPLETED'",
                (source_run_id,),
            ).fetchone()
            if not run or run["status"] != "RUNNING":
                raise ValueError("RUN_NOT_RUNNING")
            if not source:
                raise ValueError("SOURCE_RUN_NOT_COMPLETED")
            if run["analysis_id"] != source["analysis_id"]:
                raise ValueError("SOURCE_RUN_ANALYSIS_MISMATCH")
            connection.execute(
                """INSERT INTO reverse_quality_field_result(run_id,field_name,result_json)
                   SELECT ?,field_name,result_json FROM reverse_quality_field_result WHERE run_id=?""",
                (run_id, source_run_id),
            )
            connection.execute(
                """INSERT INTO reverse_quality_evidence(run_id,evidence_id,evidence_json)
                   SELECT ?,evidence_id,evidence_json FROM reverse_quality_evidence WHERE run_id=?""",
                (run_id, source_run_id),
            )
            connection.execute(
                """INSERT INTO reverse_quality_missing_information(
                       missing_id,analysis_id,run_id,field_name,reason,question,evidence_needed,status,answer,reviewer)
                   SELECT 'RQM-' || lower(hex(randomblob(16))), ?, ?, field_name,reason,question,evidence_needed,
                          status,answer,reviewer
                   FROM reverse_quality_missing_information WHERE run_id=?""",
                (run["analysis_id"], run_id, source_run_id),
            )
            scene = connection.execute(
                "SELECT * FROM reverse_quality_scene_match WHERE run_id=? ORDER BY match_id DESC LIMIT 1",
                (source_run_id,),
            ).fetchone()
            if scene:
                connection.execute(
                    """INSERT INTO reverse_quality_scene_match(
                           analysis_id,run_id,status,matched_scene_id,match_reason,missing_condition,reviewed,reviewer)
                       VALUES(?,?,?,?,?,?,0,'')""",
                    (run["analysis_id"], run_id, scene["status"], scene["matched_scene_id"],
                     scene["match_reason"], scene["missing_condition"]),
                )
            connection.execute(
                """UPDATE reverse_quality_run
                   SET status='COMPLETED',input_json=?,model=?,error='',reused_from_run_id=?,
                       completed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                   WHERE run_id=?""",
                (source["input_json"], source["model"], source_run_id, run_id),
            )
            if self._can_promote(connection, run["analysis_id"], run["run_seq"]):
                connection.execute(
                    """UPDATE reverse_quality_analysis
                       SET latest_valid_run_id=?,product_code=?,taxonomy_version_id=?,
                           status='PENDING_REVIEW',updated_at=CURRENT_TIMESTAMP
                       WHERE analysis_id=?""",
                    (run_id, run["product_code"], run["taxonomy_version_id"], run["analysis_id"]),
                )

    def fail_run(self, run_id: str, error: str) -> None:
        with self._transaction() as connection:
            run = connection.execute("SELECT * FROM reverse_quality_run WHERE run_id=?", (run_id,)).fetchone()
            if not run:
                raise KeyError(run_id)
            if run["status"] != "RUNNING":
                return
            connection.execute(
                """UPDATE reverse_quality_run
                   SET status='FAILED',error=?,completed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                   WHERE run_id=?""",
                (str(error or "")[:2000], run_id),
            )
            connection.execute(
                """UPDATE reverse_quality_analysis
                   SET status=CASE WHEN latest_valid_run_id IS NULL THEN 'FAILED' ELSE status END,
                       updated_at=CURRENT_TIMESTAMP
                   WHERE analysis_id=?""",
                (run["analysis_id"],),
            )

    def _latest_reviewed_fields(self, connection: sqlite3.Connection, run_id: str,
                                ai_fields: dict[str, Any]) -> dict[str, Any]:
        reviewed = copy.deepcopy(ai_fields)
        rows = connection.execute(
            """SELECT * FROM reverse_quality_human_review
               WHERE run_id=? AND target_type='FIELD'
               ORDER BY review_id""",
            (run_id,),
        ).fetchall()
        for row in rows:
            if row["field_name"]:
                reviewed[row["field_name"]] = _loads(row["new_json"], reviewed.get(row["field_name"], {}))
        return reviewed

    def get_latest(self, canonical_itr: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT analysis.*,run.run_id,run.run_seq,run.product_code AS run_product_code,
                          run.taxonomy_version_id AS run_taxonomy_version_id,
                          run.source_hash,run.input_json,run.model,run.error,
                          run.started_at,run.completed_at
                   FROM reverse_quality_analysis analysis
                   JOIN reverse_quality_run run ON run.run_id=analysis.latest_valid_run_id
                   WHERE analysis.canonical_itr=? AND run.status='COMPLETED'""",
                (canonical_itr,),
            ).fetchone()
            if not row:
                return None
            ai_fields = {
                item["field_name"]: _loads(item["result_json"], {})
                for item in connection.execute(
                    "SELECT field_name,result_json FROM reverse_quality_field_result WHERE run_id=? ORDER BY field_name",
                    (row["run_id"],),
                ).fetchall()
            }
            review_fields = self._latest_reviewed_fields(connection, row["run_id"], ai_fields)
            evidence = {
                item["evidence_id"]: _loads(item["evidence_json"], {})
                for item in connection.execute(
                    "SELECT evidence_id,evidence_json FROM reverse_quality_evidence WHERE run_id=? ORDER BY evidence_id",
                    (row["run_id"],),
                ).fetchall()
            }
            input_payload = _loads(row["input_json"], {})
            if isinstance(input_payload, dict):
                input_payload["evidence"] = evidence
            missing = []
            for item in connection.execute(
                """SELECT * FROM reverse_quality_missing_information
                   WHERE run_id=? ORDER BY created_at,missing_id""",
                (row["run_id"],),
            ).fetchall():
                value = dict(item)
                value["evidence_needed"] = _loads(value.get("evidence_needed"), [])
                missing.append(value)
            scene_row = connection.execute(
                "SELECT * FROM reverse_quality_scene_match WHERE run_id=? ORDER BY match_id DESC LIMIT 1",
                (row["run_id"],),
            ).fetchone()
            scene = {
                "status": scene_row["status"] if scene_row else "NEED_REVIEW",
                "matched_scene_id": scene_row["matched_scene_id"] if scene_row else "",
                "match_reason": scene_row["match_reason"] if scene_row else "",
                "missing_condition": scene_row["missing_condition"] if scene_row else "",
                "reviewed": bool(scene_row["reviewed"]) if scene_row else False,
                "reviewer": scene_row["reviewer"] if scene_row else "",
            }
            identity = {
                "canonical_itr": row["canonical_itr"],
                "product_code": row["run_product_code"] or row["product_code"],
                "taxonomy_version_id": row["run_taxonomy_version_id"] or row["taxonomy_version_id"] or "",
                "source_hash": row["source_hash"],
            }
            result = ReverseQualityResult(
                result_version=RESULT_VERSION,
                analysis_id=row["analysis_id"],
                run_id=row["run_id"],
                status=row["status"],
                identity=identity,
                fields=review_fields,
                missing_information=missing,
                scene_match=scene,
                model=row["model"] or "",
                run_seq=int(row["run_seq"]),
                started_at=row["started_at"] or "",
                completed_at=row["completed_at"] or "",
            )
            return {
                "result_version": RESULT_VERSION,
                "analysis_id": row["analysis_id"],
                "run_id": row["run_id"],
                "run_seq": int(row["run_seq"]),
                "started_at": row["started_at"] or "",
                "completed_at": row["completed_at"] or "",
                "canonical_itr": row["canonical_itr"],
                "source_hash": row["source_hash"],
                "product_code": row["run_product_code"] or row["product_code"],
                "taxonomy_version_id": row["run_taxonomy_version_id"] or row["taxonomy_version_id"],
                "input": input_payload,
                "ai": ai_fields,
                "review": review_fields,
                "missing_information": missing,
                "scene_match": scene,
                "scene_match_status": scene["status"],
                "matched_scene_id": scene["matched_scene_id"],
                "match_reason": scene["match_reason"],
                "missing_condition": scene["missing_condition"],
                "match_reviewed": scene["reviewed"],
                "status": row["status"],
                "model": row["model"] or "",
                "error": row["error"] or "",
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "result": result.to_dict(),
            }

    def list_runs(self, canonical_itr: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                """SELECT run.* FROM reverse_quality_run run
                   JOIN reverse_quality_analysis analysis USING(analysis_id)
                   WHERE analysis.canonical_itr=? ORDER BY run.run_seq DESC""",
                (canonical_itr,),
            ).fetchall()]

    def save_field_review(self, canonical_itr: str, *, field_name: str, action: str,
                          old: dict[str, Any], new: dict[str, Any], reviewer: str,
                          analysis_status: str) -> None:
        with self._transaction() as connection:
            current = connection.execute(
                """SELECT analysis.analysis_id,analysis.latest_valid_run_id
                   FROM reverse_quality_analysis analysis WHERE analysis.canonical_itr=?""",
                (canonical_itr,),
            ).fetchone()
            if not current or not current["latest_valid_run_id"]:
                raise KeyError(canonical_itr)
            connection.execute(
                """INSERT INTO reverse_quality_human_review(
                       analysis_id,run_id,target_type,field_name,action,old_json,new_json,reviewer)
                   VALUES(?,?,'FIELD',?,?,?,?,?)""",
                (current["analysis_id"], current["latest_valid_run_id"], field_name, action,
                 _json(old), _json(new), reviewer),
            )
            connection.execute(
                "UPDATE reverse_quality_analysis SET status=?,updated_at=CURRENT_TIMESTAMP WHERE analysis_id=?",
                (analysis_status, current["analysis_id"]),
            )

    def resolve_missing_information(self, canonical_itr: str, *, missing_id: str, status: str,
                                    answer: str, reviewer: str) -> dict[str, Any]:
        if status not in {"CONFIRMED", "NOT_APPLICABLE"}:
            raise ValueError("MISSING_INFORMATION_STATUS_INVALID")
        reviewer = reviewer.strip()
        if not reviewer:
            raise ValueError("MISSING_INFORMATION_REVIEWER_REQUIRED")
        with self._transaction() as connection:
            current = connection.execute(
                """SELECT analysis_id,latest_valid_run_id FROM reverse_quality_analysis
                   WHERE canonical_itr=?""",
                (canonical_itr,),
            ).fetchone()
            if not current or not current["latest_valid_run_id"]:
                raise KeyError(canonical_itr)
            row = connection.execute(
                """SELECT * FROM reverse_quality_missing_information
                   WHERE missing_id=? AND analysis_id=? AND run_id=?""",
                (missing_id, current["analysis_id"], current["latest_valid_run_id"]),
            ).fetchone()
            if not row:
                raise KeyError(missing_id)
            old = dict(row)
            connection.execute(
                """UPDATE reverse_quality_missing_information
                   SET status=?,answer=?,reviewer=?,updated_at=CURRENT_TIMESTAMP
                   WHERE missing_id=?""",
                (status, answer.strip(), reviewer, missing_id),
            )
            new = dict(connection.execute(
                "SELECT * FROM reverse_quality_missing_information WHERE missing_id=?",
                (missing_id,),
            ).fetchone())
            connection.execute(
                """INSERT INTO reverse_quality_human_review(
                       analysis_id,run_id,target_type,field_name,action,old_json,new_json,reviewer)
                   VALUES(?,?,'MISSING_INFORMATION',?,?,?,?,?)""",
                (current["analysis_id"], current["latest_valid_run_id"], missing_id, status,
                 _json(old), _json(new), reviewer),
            )
            return new

    def save_scene_review(self, canonical_itr: str, *, scene_match: dict[str, Any], reviewer: str,
                          analysis_status: str) -> None:
        with self._transaction() as connection:
            current = connection.execute(
                """SELECT analysis_id,latest_valid_run_id FROM reverse_quality_analysis
                   WHERE canonical_itr=?""",
                (canonical_itr,),
            ).fetchone()
            if not current or not current["latest_valid_run_id"]:
                raise KeyError(canonical_itr)
            connection.execute(
                """INSERT INTO reverse_quality_scene_match(
                       analysis_id,run_id,status,matched_scene_id,match_reason,missing_condition,reviewed,reviewer)
                   VALUES(?,?,?,?,?,?,1,?)""",
                (
                    current["analysis_id"], current["latest_valid_run_id"],
                    str(scene_match.get("scene_match_status") or scene_match.get("status") or "NEED_REVIEW"),
                    str(scene_match.get("matched_scene_id") or ""),
                    str(scene_match.get("match_reason") or ""),
                    str(scene_match.get("missing_condition") or ""), reviewer,
                ),
            )
            connection.execute(
                "UPDATE reverse_quality_analysis SET status=?,updated_at=CURRENT_TIMESTAMP WHERE analysis_id=?",
                (analysis_status, current["analysis_id"]),
            )

    def _set_analysis_status(self, canonical_itr: str, status: str) -> None:
        with self._transaction() as connection:
            connection.execute(
                "UPDATE reverse_quality_analysis SET status=?,updated_at=CURRENT_TIMESTAMP WHERE canonical_itr=?",
                (status, canonical_itr),
            )

    def _discard_analysis(self, canonical_itr: str) -> None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT analysis_id FROM reverse_quality_analysis WHERE canonical_itr=?",
                (canonical_itr,),
            ).fetchone()
            if not row:
                return
            analysis_id = row["analysis_id"]
            run_ids = [item["run_id"] for item in connection.execute(
                "SELECT run_id FROM reverse_quality_run WHERE analysis_id=?", (analysis_id,)
            ).fetchall()]
            for run_id in run_ids:
                connection.execute("DELETE FROM reverse_quality_human_review WHERE run_id=?", (run_id,))
                connection.execute("DELETE FROM reverse_quality_scene_match WHERE run_id=?", (run_id,))
                connection.execute("DELETE FROM reverse_quality_missing_information WHERE run_id=?", (run_id,))
                connection.execute("DELETE FROM reverse_quality_field_result WHERE run_id=?", (run_id,))
                connection.execute("DELETE FROM reverse_quality_evidence WHERE run_id=?", (run_id,))
            connection.execute("DELETE FROM reverse_quality_run WHERE analysis_id=?", (analysis_id,))
            connection.execute("DELETE FROM reverse_quality_analysis WHERE analysis_id=?", (analysis_id,))

    def migrate_legacy(self, legacy_repository: Any) -> int:
        """One-way, idempotent import from PATCH58 tables if they exist in the old DB."""
        with legacy_repository.connect() as legacy:
            tables = {row[0] for row in legacy.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "reverse_quality_analysis" not in tables:
                return 0
            analyses = [dict(row) for row in legacy.execute("SELECT * FROM reverse_quality_analysis")]
            field_reviews = [dict(row) for row in legacy.execute(
                "SELECT * FROM reverse_quality_field_review ORDER BY review_id"
            )] if "reverse_quality_field_review" in tables else []
            match_reviews = [dict(row) for row in legacy.execute(
                "SELECT * FROM reverse_quality_match_review ORDER BY review_id"
            )] if "reverse_quality_match_review" in tables else []
        migrated = 0
        by_field: dict[str, list[dict[str, Any]]] = {}
        for review in field_reviews:
            by_field.setdefault(review["canonical_itr"], []).append(review)
        by_match: dict[str, list[dict[str, Any]]] = {}
        for review in match_reviews:
            by_match.setdefault(review["canonical_itr"], []).append(review)
        for legacy in analyses:
            canonical_itr = legacy["canonical_itr"]
            if self.get_latest(canonical_itr):
                continue
            input_payload = _loads(legacy.get("input_json"), {})
            ai_fields = _loads(legacy.get("ai_json"), {})
            review_fields = _loads(legacy.get("review_json"), ai_fields)
            run = self.start_run(
                canonical_itr=canonical_itr,
                product_code=legacy.get("product_code") or "",
                taxonomy_version_id=legacy.get("taxonomy_version_id") or "",
                source_hash=legacy.get("source_hash") or "",
                input_payload=input_payload,
            )
            try:
                self.complete_run(
                    run["run_id"], fields=ai_fields,
                    evidence=(input_payload.get("evidence") or {}) if isinstance(input_payload, dict) else {},
                    scene_match={
                        "status": legacy.get("scene_match_status") or "NEED_REVIEW",
                        "matched_scene_id": legacy.get("matched_scene_id") or "",
                        "match_reason": legacy.get("match_reason") or "",
                        "missing_condition": legacy.get("missing_condition") or "",
                    },
                    model=legacy.get("model") or "", input_payload=input_payload,
                    missing_information=[],
                )
                for review in by_field.get(canonical_itr, []):
                    self.save_field_review(
                        canonical_itr, field_name=review.get("field_name") or "",
                        action=review.get("action") or "CONFIRMED",
                        old=_loads(review.get("old_json"), {}), new=_loads(review.get("new_json"), {}),
                        reviewer=review.get("reviewer") or "LEGACY_MIGRATION", analysis_status="IN_REVIEW",
                    )
                if not by_field.get(canonical_itr):
                    for name, value in review_fields.items():
                        original = ai_fields.get(name) or {}
                        if value != original or value.get("review_status") not in {None, "", "PENDING"}:
                            self.save_field_review(
                                canonical_itr, field_name=name,
                                action=value.get("review_status") or "EDITED",
                                old=original, new=value,
                                reviewer="LEGACY_MIGRATION", analysis_status="IN_REVIEW",
                            )
                for review in by_match.get(canonical_itr, []):
                    new = _loads(review.get("new_json"), {})
                    self.save_scene_review(
                        canonical_itr, scene_match=new,
                        reviewer=review.get("reviewer") or "LEGACY_MIGRATION", analysis_status="IN_REVIEW",
                    )
                self._set_analysis_status(canonical_itr, legacy.get("status") or "PENDING_REVIEW")
                migrated += 1
            except Exception as exc:
                self._discard_analysis(canonical_itr)
                raise RuntimeError(
                    f"LEGACY_MIGRATION_FAILED:{canonical_itr}: {exc}"
                ) from exc
        return migrated
