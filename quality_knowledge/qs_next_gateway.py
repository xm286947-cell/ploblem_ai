"""Isolated, read-only Quality Scenario entry gateway for the mature material DB.

Opt-in router only. No schema migration, auto generation, Provider or writes.
The material/workbench selection is not itself proof of scenario evidence.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import json
import re
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException

CONTRACT_VERSION = "quality-scenario-material-read/v1"
ENTRIES = {"cs": ("ITR_CS", "THOROUGH_SOLUTION_ORDER"),
           "missed-test": ("ESCAPE_ANALYSIS", "MISSED_TEST_ANALYSIS"),
           "software-assessment": ("SOFTWARE_OPERATION", None)}
FORMAL = {"ITR_CS": "THOROUGH_SOLUTION_ORDER",
          "ESCAPE_ANALYSIS": "MISSED_TEST_ANALYSIS"}
ALLOWED_LINKS = ("LINKED", "MANUAL_LINKED")
DOMAIN = {"软件": "SOFTWARE", "SOFTWARE": "SOFTWARE",
          "硬件": "HARDWARE", "HARDWARE": "HARDWARE",
          "机械": "MECHANICAL", "MECHANICAL": "MECHANICAL"}
DOMAIN_FIELDS = ("问题信息_问题领域", "问题领域", "技术根因分析与纠正_问题类型")


class EntryGateError(ValueError):
    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status
        super().__init__(code)


@contextmanager
def read_only_db(db_path: str | Path):
    db = Path(db_path).resolve(strict=True)
    # URI mode=ro is authoritative; query_only additionally prevents writes.
    con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=ON")
        yield con
    finally:
        con.close()


def _source(con: sqlite3.Connection, material_id: str) -> dict[str, Any]:
    row = con.execute("""SELECT m.material_id,m.group_id,m.material_type,
           m.business_key,m.canonical_itr,m.version_no,m.source_hash,m.raw_json,
           g.group_code FROM source_material m JOIN data_group g
           ON m.group_id=g.group_id WHERE m.material_id=?""", (material_id,)).fetchone()
    if not row:
        raise EntryGateError("MATERIAL_NOT_FOUND", 404)
    item = dict(row)
    try:
        item["raw"] = json.loads(item.pop("raw_json"))
    except (ValueError, TypeError):
        raise EntryGateError("INVALID_ORIGINAL_RAW_RECORD") from None
    if not isinstance(item["raw"], dict):
        raise EntryGateError("INVALID_ORIGINAL_RAW_RECORD")
    if not isinstance(item["version_no"], int) or item["version_no"] <= 0:
        raise EntryGateError("INVALID_ORIGINAL_REVISION")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", str(item["source_hash"])):
        raise EntryGateError("INVALID_ORIGINAL_SOURCE_HASH")
    latest = con.execute("""SELECT material_id FROM source_material
        WHERE group_id=? AND business_key=?
        ORDER BY version_no DESC,created_at DESC,material_id DESC LIMIT 1""",
        (item["group_id"], item["business_key"])).fetchone()
    if latest and latest[0] != material_id:
        raise EntryGateError("STALE_SOURCE_REVISION")
    return item


def _explicit_cs_domains(raw: dict[str, Any]) -> list[str]:
    labels = []
    for key in DOMAIN_FIELDS:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        tokens = [token.strip().upper() for token in re.split(r"[/、，,;；|+\s]+", value.strip())]
        # Unknown enumerations remain unconfirmed, never infer from description.
        if not tokens or any(token not in DOMAIN for token in tokens):
            return []
        for token in tokens:
            if DOMAIN[token] not in labels:
                labels.append(DOMAIN[token])
        break
    return labels


def _linked_issue(con: sqlite3.Connection, material_id: str) -> str | None:
    rows = con.execute("""SELECT DISTINCT knowledge_id FROM issue_material_link
        WHERE material_id=? AND link_status IN ('LINKED','MANUAL_LINKED')""",
        (material_id,)).fetchall()
    if len(rows) > 1:
        raise EntryGateError("AMBIGUOUS_SOURCE_RELATION")
    return str(rows[0][0]) if rows else None


def _source_ref(src: dict[str, Any], *, relation: str) -> dict[str, Any]:
    return {"material_id": src["material_id"], "material_type": src["material_type"],
            "source_ref": f'{src["material_type"]}:{src["material_id"]}',
            "formal_source_type": FORMAL.get(src["material_type"]),
            "source_hash": src["source_hash"], "version_no": src["version_no"],
            "canonical_problem_ref": src.get("canonical_itr") or "",
            "relation": relation}


def resolve_entry(db_path: str | Path, workbench: str, material_id: str) -> dict[str, Any]:
    if workbench not in ENTRIES:
        raise EntryGateError("UNSUPPORTED_WORKBENCH", 400)
    if not isinstance(material_id, str) or not material_id.strip() or "/" in material_id:
        raise EntryGateError("INVALID_MATERIAL_ID", 400)
    with read_only_db(db_path) as con:
        entry = _source(con, material_id.strip())
        expected, formal_type = ENTRIES[workbench]
        if entry["material_type"] != expected:
            raise EntryGateError("ENTRY_MATERIAL_TYPE_MISMATCH", 400)
        if workbench == "cs":
            domains = _explicit_cs_domains(entry["raw"])
        else:
            # A software-only workbench never silently promotes hardware/mechanical.
            stated = _explicit_cs_domains(entry["raw"])
            if stated and set(stated) != {"SOFTWARE"}:
                raise EntryGateError("DOMAIN_NOT_ALLOWED_FOR_WORKBENCH")
            domains = ["SOFTWARE"]
        sources = [_source_ref(entry, relation="ENTRY")] if formal_type else []
        issue_ref = _linked_issue(con, material_id)
        if issue_ref:
            links = con.execute("""SELECT DISTINCT l.material_id,l.link_status FROM issue_material_link l
                JOIN source_material m ON m.material_id=l.material_id
                WHERE l.knowledge_id=? AND l.link_status IN ('LINKED','MANUAL_LINKED')
                  AND m.material_type IN ('ITR_CS','ESCAPE_ANALYSIS')
                  AND l.material_id<>? ORDER BY l.material_id""", (issue_ref, material_id)).fetchall()
            for item in links:
                src = _source(con, item["material_id"])
                if entry.get("canonical_itr") and src.get("canonical_itr") and (
                    entry["canonical_itr"].strip().upper() != src["canonical_itr"].strip().upper()
                ):
                    raise EntryGateError("SOURCE_CONTEXT_MISMATCH")
                related_domains = _explicit_cs_domains(src["raw"]) if src["material_type"] == "ITR_CS" else ["SOFTWARE"]
                if src["material_type"] == "ESCAPE_ANALYSIS" and "SOFTWARE" not in domains:
                    continue  # missed-test is software-only, never coverage for hardware/mechanical
                if workbench != "cs" and related_domains and "SOFTWARE" not in related_domains:
                    continue  # not a valid formal input for this software-only entry
                sources.append(_source_ref(src, relation=item["link_status"]))
        coverage = ("NONE" if not sources else "FULL" if {s["formal_source_type"] for s in sources} ==
                    {"THOROUGH_SOLUTION_ORDER", "MISSED_TEST_ANALYSIS"} else "PARTIAL")
        state = ("DOMAIN_REVIEW_REQUIRED" if not domains else
                 "FORMAL_SOURCE_REQUIRED" if not sources else "READY_FOR_SOURCE_READ")
        return {"contract_version": CONTRACT_VERSION,
                "entry_workbench": workbench, "entry_material": _source_ref(entry, relation="ENTRY"),
                "domains": domains, "domain_status": "EXPLICIT" if workbench == "cs" and domains else
                    "UNCONFIRMED" if workbench == "cs" else "WORKBENCH_POLICY",
                "problem_ref_context": entry.get("canonical_itr") or "",
                "linked_issue_context": issue_ref,
                "formal_source_reads": sources, "source_coverage": coverage,
                "status": state, "can_extract_facts": state == "READY_FOR_SOURCE_READ",
                "candidate_created": False, "published": False}


def create_router(db_path: str | Path) -> APIRouter:
    """Opt-in only; existing baseline never automatically registers this router."""
    router = APIRouter(tags=["QS Next Isolated Entry Gateway"])

    @router.get("/api/v2/qs-next/entry/v1/{workbench}/{material_id}")
    def entry_plan(workbench: str, material_id: str) -> dict[str, Any]:
        try:
            return resolve_entry(db_path, workbench, material_id)
        except EntryGateError as e:
            raise HTTPException(e.status, detail=e.code) from e

    return router
