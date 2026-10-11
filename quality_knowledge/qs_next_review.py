"""Opt-in Stage5 candidate review. Separate SQLite, append-only audit, no legacy writes.

A *trusted* actor and permission resolver are mandatory for HTTP use. This is
not mounted by the old app; it has no default authentication bypass. A human
confirmation is not the same as publication to historical ScenarioAssets.
"""
from __future__ import annotations

from contextlib import contextmanager

import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Mapping
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from quality_knowledge.qs_next_candidate_preview import (
    FIELD_ALIASES, DIMENSIONS, DOMAIN_FIELDS, preview_one_issue,
)
from quality_knowledge.qs_next_gateway import EntryGateError

CONTRACT_VERSION = "qs-review-audit/v1"
REQUIRED_FIELDS = (
    "lifecycle_code", "activity_code", "user_type",
    "experience_requirement", "trigger_conditions", "validation_direction",
)
FAILURE_FIELDS = (
    "symptom", "failure_mode", "software_failure_mode",
    "hardware_failure_mode", "mechanical_failure_mode",
)


class ReviewError(ValueError):
    def __init__(self, code: str, http_status: int = 409):
        self.code, self.http_status = code, http_status
        super().__init__(code)


def _clean(value: Any, code: str, maximum: int = 1000) -> str:
    if not isinstance(value, str):
        raise ReviewError(code, 400)
    text = value.strip()
    if not text or len(text) > maximum or any(ord(c) < 32 for c in text):
        raise ReviewError(code, 400)
    return text


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _preview(db: str | Path, workbench: str, material_id: str) -> dict[str, Any]:
    try:
        result = preview_one_issue(str(db), workbench, material_id)
    except EntryGateError as exc:
        raise ReviewError(exc.code, exc.status) from exc
    if result["candidate"] is None:
        raise ReviewError(result["blockers"][0])
    return result["candidate"]


class ReviewStore:
    """Owned solely by the new opt-in feature: never pass the source DB here."""

    def __init__(self, source_db: str | Path, review_db: str | Path):
        self.source_db = Path(source_db).resolve()
        self.review_db = Path(review_db).resolve()
        if self.source_db == self.review_db:
            raise ReviewError("REVIEW_STORE_MUST_BE_ISOLATED")
        if not self.source_db.is_file():
            raise ReviewError("SOURCE_DB_NOT_FOUND", 404)
        self.review_db.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS qs_next_review(
                    review_id TEXT PRIMARY KEY, workbench TEXT NOT NULL,
                    material_id TEXT NOT NULL, preview_hash TEXT NOT NULL,
                    original_json TEXT NOT NULL, edits_json TEXT NOT NULL,
                    resolutions_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN
                        ('IN_REVIEW','CONFIRMED','REJECTED')),
                    revision INTEGER NOT NULL,
                    created_by TEXT NOT NULL, updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS qs_next_review_event(
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_id TEXT NOT NULL REFERENCES qs_next_review(review_id),
                    revision INTEGER NOT NULL, action TEXT NOT NULL,
                    actor TEXT NOT NULL, reason TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(review_id,revision)
                );
            """)

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.review_db, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _record(db, rid: str) -> dict[str, Any]:
        row = db.execute("SELECT * FROM qs_next_review WHERE review_id=?", (rid,)).fetchone()
        if row is None:
            raise ReviewError("REVIEW_NOT_FOUND", 404)
        return dict(row)

    @staticmethod
    def _view(row: Mapping[str, Any]) -> dict[str, Any]:
        original = json.loads(row["original_json"])
        edits = json.loads(row["edits_json"])
        resolved = json.loads(row["resolutions_json"])
        fields = {**original["legacy_compatible_fields"],
                  **{name: value["value"] for name, value in edits.items()}}
        missing = [name for name in REQUIRED_FIELDS if not fields.get(name)]
        if not any(fields.get(k) for k in FAILURE_FIELDS):
            missing.append("failure_symptom_or_mode")
        conflicts = [x["field"] for x in original["conflicts"] if x["field"] not in resolved]
        return {
            "review_id": row["review_id"], "status": row["state"],
            "revision": row["revision"], "entry_workbench": row["workbench"],
            "entry_material_id": row["material_id"],
            "candidate_preview_id": original["preview_id"],
            "problem_ref_context": original["problem_ref_context"],
            "problem_domains": original["problem_domains"],
            "source_refs": original["source_refs"],
            "five_dimensions": {
                dimension: {field: fields[field] for field in names if field in fields}
                for dimension, names in DIMENSIONS.items()
            },
            "effective_fields": fields, "human_edits": edits,
            "original_field_evidence": original["all_field_evidence"],
            "source_conflicts": original["conflicts"],
            "conflict_resolutions": resolved,
            "unresolved_conflicts": conflicts,
            "required_missing": missing,
            "publish_ready": False,  # review confirmation != publication
        }

    def _event(self, db, rid: str, revision: int, action: str,
               actor: str, reason: str, detail: Any):
        db.execute("""INSERT INTO qs_next_review_event
            (review_id,revision,action,actor,reason,details_json)
            VALUES(?,?,?,?,?,?)""",
            (rid,revision,action,actor,reason,_json(detail)))

    def _ensure_current(self, row: Mapping[str, Any]):
        fresh = _preview(self.source_db, row["workbench"], row["material_id"])
        if _hash(fresh) != row["preview_hash"]:
            raise ReviewError("SOURCE_SNAPSHOT_CHANGED")
        if fresh["preview_id"] != row["review_id"]:
            raise ReviewError("SOURCE_IDENTITY_CHANGED")

    def start(self, workbench: str, material_id: str, actor: str) -> dict[str, Any]:
        actor = _clean(actor, "TRUSTED_ACTOR_REQUIRED", 128)
        candidate = _preview(self.source_db, workbench, material_id)
        rid = candidate["preview_id"]
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            found = db.execute("SELECT * FROM qs_next_review WHERE review_id=?", (rid,)).fetchone()
            if found:
                row = dict(found)
                self._ensure_current(row)
                return self._view(row)  # retries are idempotent; never reset edits
            db.execute("""INSERT INTO qs_next_review
                (review_id,workbench,material_id,preview_hash,original_json,
                 edits_json,resolutions_json,state,revision,created_by,updated_by)
                 VALUES(?,?,?,?,?,?,?,'IN_REVIEW',1,?,?)""",
                (rid,workbench,material_id,_hash(candidate),_json(candidate),
                 "{}", "{}",actor,actor))
            self._event(db,rid,1,"START",actor,"候选进入人工审核",{"preview_id":rid})
            return self._view(self._record(db,rid))

    def get(self, review_id: str) -> dict[str, Any]:
        with self._db() as db:
            return self._view(self._record(db,review_id))

    def audit(self, review_id: str) -> list[dict[str, Any]]:
        with self._db() as db:
            self._record(db,review_id)
            return [dict(item) for item in db.execute(
                """SELECT event_id,review_id,revision,action,actor,reason,
                    details_json,created_at FROM qs_next_review_event
                    WHERE review_id=? ORDER BY event_id""",(review_id,))]

    def revise(self, rid: str, actor: str, expected_revision: int,
               changes: Mapping[str,str], reason: str,
               resolved_conflicts: Mapping[str,str] | None = None) -> dict[str, Any]:
        actor = _clean(actor,"TRUSTED_ACTOR_REQUIRED",128)
        reason = _clean(reason,"REVIEW_REASON_REQUIRED",500)
        if not isinstance(expected_revision,int) or isinstance(expected_revision,bool):
            raise ReviewError("REVISION_REQUIRED",400)
        if not isinstance(changes,dict) or not isinstance(resolved_conflicts or {},dict):
            raise ReviewError("INVALID_REVIEW_PATCH",400)
        if not changes and not resolved_conflicts:
            raise ReviewError("EMPTY_REVIEW_PATCH",400)
        if any(key not in FIELD_ALIASES for key in changes):
            raise ReviewError("UNKNOWN_REVIEW_FIELD",400)
        clean_changes={key:_clean(value,"INVALID_HUMAN_FIELD_VALUE") for key,value in changes.items()}
        resolutions=resolved_conflicts or {}
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row=self._record(db,rid)
            self._ensure_current(row)
            if row["state"]!="IN_REVIEW":
                raise ReviewError("REVIEW_ALREADY_FINAL")
            if row["revision"]!=expected_revision:
                raise ReviewError("REVIEW_REVISION_CONFLICT")
            original=json.loads(row["original_json"])
            domains=set(original["problem_domains"])
            for field in clean_changes:
                required_domain=DOMAIN_FIELDS.get(field)
                if (required_domain and required_domain not in domains) or (
                    field=="escape_reason" and "SOFTWARE" not in domains
                ):
                    raise ReviewError("REVIEW_FIELD_DOMAIN_MISMATCH",400)
            allowed={x["field"] for x in original["conflicts"]}
            if any(key not in allowed for key in resolutions):
                raise ReviewError("INVALID_CONFLICT_RESOLUTION_FIELD",400)
            if any(key not in clean_changes for key in resolutions):
                raise ReviewError("CONFLICT_RESOLUTION_REQUIRES_EXPLICIT_VALUE",400)
            for value in resolutions.values():
                _clean(value,"CONFLICT_RESOLUTION_REASON_REQUIRED",500)
            edits=json.loads(row["edits_json"])
            for field,value in clean_changes.items():
                edits[field]={"value":value,"editor":actor,"reason":reason,
                              "provenance":"HUMAN_CONFIRMED","revision":expected_revision+1}
            old_resolutions=json.loads(row["resolutions_json"])
            for field,note in resolutions.items():
                old_resolutions[field]={"actor":actor,"reason":note,
                                        "revision":expected_revision+1}
            revision=expected_revision+1
            db.execute("""UPDATE qs_next_review SET edits_json=?,resolutions_json=?,
                revision=?,updated_by=?,updated_at=CURRENT_TIMESTAMP WHERE review_id=?""",
                (_json(edits),_json(old_resolutions),revision,actor,rid))
            self._event(db,rid,revision,"REVISE",actor,reason,{
                "changes":clean_changes,"resolved_conflicts":resolutions})
            return self._view(self._record(db,rid))

    def decide(self, rid: str, actor: str, expected_revision: int,
               decision: str, reason: str,
               taxonomy_verified: Callable[[Mapping[str, Any]], bool] | None = None
               ) -> dict[str, Any]:
        actor=_clean(actor,"TRUSTED_ACTOR_REQUIRED",128)
        reason=_clean(reason,"REVIEW_REASON_REQUIRED",500)
        if decision not in ("CONFIRM","REJECT"):
            raise ReviewError("INVALID_REVIEW_DECISION",400)
        if not isinstance(expected_revision,int) or isinstance(expected_revision,bool):
            raise ReviewError("REVISION_REQUIRED",400)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row=self._record(db,rid)
            self._ensure_current(row)
            if row["state"]!="IN_REVIEW":
                raise ReviewError("REVIEW_ALREADY_FINAL")
            if row["revision"]!=expected_revision:
                raise ReviewError("REVIEW_REVISION_CONFLICT")
            view=self._view(row)
            if decision=="CONFIRM":
                if view["required_missing"]:
                    raise ReviewError("REQUIRED_SCENARIO_FIELDS_MISSING")
                if view["unresolved_conflicts"]:
                    raise ReviewError("UNRESOLVED_SOURCE_CONFLICTS")
                if taxonomy_verified is None or taxonomy_verified(view) is not True:
                    raise ReviewError("CONTROLLED_TAXONOMY_NOT_VERIFIED")
            state="CONFIRMED" if decision=="CONFIRM" else "REJECTED"
            revision=expected_revision+1
            db.execute("""UPDATE qs_next_review SET state=?,revision=?,updated_by=?,
                updated_at=CURRENT_TIMESTAMP WHERE review_id=?""",
                (state,revision,actor,rid))
            self._event(db,rid,revision,decision,actor,reason,{
                "state":state,"taxonomy_checked":decision=="CONFIRM",
                "effective_fields_sha256":_hash(view["effective_fields"])})
            return self._view(self._record(db,rid))


class RevisePayload(BaseModel):
    expected_revision: int
    changes: dict[str,str] = Field(default_factory=dict)
    reason: str
    resolved_conflicts: dict[str,str] = Field(default_factory=dict)


class DecidePayload(BaseModel):
    expected_revision: int
    decision: str
    reason: str


def create_review_router(
    source_db: str | Path, review_db: str | Path,
    *, trusted_actor: Callable[[Request], str] | None = None,
    authorize: Callable[[str,str,str], bool] | None = None,
    taxonomy_verified: Callable[[Mapping[str, Any]], bool] | None = None,
) -> APIRouter:
    """Explicit opt-in only. No implicit actor, permission or taxonomy bypass."""
    store=ReviewStore(source_db,review_db)
    router=APIRouter(tags=["QS Next Human Review - Isolated"])

    def guard(request: Request, action: str, subject: str) -> str:
        if trusted_actor is None or authorize is None:
            raise HTTPException(403, detail="TRUSTED_SECURITY_BINDING_REQUIRED")
        actor=trusted_actor(request)
        if not isinstance(actor,str) or not actor.strip() or not authorize(actor,action,subject):
            raise HTTPException(403,detail="REVIEW_PERMISSION_DENIED")
        return actor

    def invoke(callback):
        try:
            return callback()
        except ReviewError as exc:
            raise HTTPException(exc.http_status,detail=exc.code) from exc

    @router.post("/api/v2/qs-next/review/v1/start/{workbench}/{material_id}")
    def start(request: Request, workbench: str, material_id: str):
        actor=guard(request,"START",f"{workbench}:{material_id}")
        return invoke(lambda:store.start(workbench,material_id,actor))

    @router.get("/api/v2/qs-next/review/v1/{review_id}")
    def get(request: Request, review_id: str):
        guard(request,"READ",review_id)
        return invoke(lambda:store.get(review_id))

    @router.get("/api/v2/qs-next/review/v1/{review_id}/audit")
    def audit(request: Request, review_id: str):
        guard(request,"READ",review_id)
        return invoke(lambda:store.audit(review_id))

    @router.post("/api/v2/qs-next/review/v1/{review_id}/revise")
    def revise(request: Request, review_id: str, payload: RevisePayload):
        actor=guard(request,"REVISE",review_id)
        return invoke(lambda:store.revise(
            review_id,actor,payload.expected_revision,payload.changes,
            payload.reason,payload.resolved_conflicts))

    @router.post("/api/v2/qs-next/review/v1/{review_id}/decide")
    def decide(request: Request, review_id: str, payload: DecidePayload):
        actor=guard(request,"DECIDE",review_id)
        return invoke(lambda:store.decide(
            review_id,actor,payload.expected_revision,payload.decision,
            payload.reason,taxonomy_verified))

    return router
