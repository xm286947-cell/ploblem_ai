"""Restored Major Case intake and CaseFeatureView domain services.

This module restores the original production mechanism without introducing a
second generic Excel-import platform:
- parser.ExcelParser remains the Excel parser/mapping implementation;
- parser.ReportMatcher remains the document matching implementation;
- MajorKnowledgeRepository remains the knowledge store;
- this module only maps those existing capabilities into MAJOR_CASE semantics.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import re
import shutil
import sqlite3
import uuid

from parser.excel_parser import ExcelParser, normalize_header
from parser.report_matcher import ReportMatcher
from quality_knowledge.materials import normalize_itr
from .document_parser import parse_document

from .repository import MajorKnowledgeRepository
from .import_governance import (
    IMPORT_GOVERNANCE_CONTRACT,
    MAPPING_CONTRACT,
    inspect_template,
    mapping_version,
)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _split_values(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    values: list[str] = []
    for item in re.split(r"[,，;；\n\r|/]+", text):
        item = item.strip()
        if item and item not in values:
            values.append(item)
    return values


def _pick_raw(raw: dict[str, Any], aliases: Iterable[str]) -> str:
    lookup = {normalize_header(key): value for key, value in raw.items()}
    for alias in aliases:
        value = lookup.get(normalize_header(alias))
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


EXTRA_ALIASES = {
    "igr": ("IGR", "IGR号", "IGR编号", "重大问题编号", "重大问题单号"),
    "title": ("重大问题标题", "问题标题", "Bug标题", "标题"),
    "related_itrs": ("关联ITR", "关联ITR号", "关联ITR单号", "ITR列表", "关联问题单"),
    "product": ("产品", "产品分类", "产品型号", "产品系列"),
    "module": ("模块", "所属模块", "功能模块"),
    "component": ("器件", "器件名称", "元器件", "物料型号", "器件型号"),
    "failure_mode": ("Failure Mode", "失效模式"),
    "failure_mechanism": ("Failure Mechanism", "失效机理", "失效机制"),
    "trigger_condition": ("Trigger Condition", "触发条件", "复现条件"),
}


SOURCE_FEATURE_MAP = {
    "original_description": "issue_fact",
    "assessment_year": "assessment_year",
    "assessment_month": "assessment_month",
    "ipmt": "ipmt",
    "spdt": "spdt",
    "responsible_department_level2": "responsible_department",
    "trc_occurrence": "trc_occurrence",
    "trc_escape": "trc_escape",
    "mrc_occurrence": "mrc_occurrence",
    "mrc_escape": "mrc_escape",
    "cause_level1": "classification_l1",
    "cause_level2": "classification_l2",
    "cause_level3": "classification_l3",
    "cause_level4": "classification_l4",
    "report_filename": "report_filename",
    "product": "product",
    "module": "module",
    "component": "component",
    "failure_mode": "failure_mode",
    "failure_mechanism": "failure_mechanism",
    "trigger_condition": "trigger_condition",
    "igr": "igr",
}

ENTRY_FEATURE_MAP = {
    "ISSUE_FACT": "issue_fact",
    "ROOT_CAUSE": "root_cause",
    "ACTION": "solution",
    "VERIFICATION": "verification",
    "TRC_OCCURRENCE": "trc_occurrence",
    "TRC_ESCAPE": "trc_escape",
    "MRC_OCCURRENCE": "mrc_occurrence",
    "MRC_ESCAPE": "mrc_escape",
    "FAILURE_MODE": "failure_mode",
    "FAILURE_MECHANISM": "failure_mechanism",
    "TRIGGER_CONDITION": "trigger_condition",
    "CLASSIFICATION": "classification",
    "COMPONENT": "component",
}


class MajorCaseRestoreService:
    """MAJOR_CASE adapter over the existing Excel/report and knowledge layers."""

    def __init__(
        self,
        repository: MajorKnowledgeRepository,
        project_root: str | Path,
    ) -> None:
        self.repository = repository
        self.project_root = Path(project_root).resolve()
        self.excel_parser = ExcelParser(self.project_root / "config/field_mapping.yaml")
        self.report_matcher = ReportMatcher(self.project_root / "config/report_matching.yaml")
        self.staging_root = self.repository.attachment_root.parent / "import_staging"
        self.staging_root.mkdir(parents=True, exist_ok=True)

    def current_mapping_version(self) -> str:
        return mapping_version(dict(self.excel_parser.field_mapping))

    def _enrich_record(self, record: dict) -> dict:
        raw = dict(record.get("raw_fields") or {})
        mapped = dict(record.get("mapped_fields") or {})
        igr = _pick_raw(raw, EXTRA_ALIASES["igr"])
        related = _pick_raw(raw, EXTRA_ALIASES["related_itrs"])
        itr_values = []
        for value in [mapped.get("itr_id"), *_split_values(related)]:
            canonical = normalize_itr(value)
            if canonical and canonical not in itr_values:
                itr_values.append(canonical)

        for key in ("product", "module", "component", "failure_mode", "failure_mechanism", "trigger_condition"):
            mapped[key] = _pick_raw(raw, EXTRA_ALIASES[key])
        mapped["igr"] = igr
        mapped["itrs"] = itr_values
        title = _pick_raw(raw, EXTRA_ALIASES["title"])
        description = str(mapped.get("original_description") or "").strip()
        title = title or description[:80] or igr or (itr_values[0] if itr_values else f"重大问题-{record['excel_row']}")

        if igr:
            source_key = f"IGR:{igr}"
        elif itr_values:
            source_key = "ITR:" + "|".join(sorted(itr_values))
        else:
            source_key = (
                f"ROW:{Path(record['source_excel']).name}:"
                f"{record['sheet_name']}:{record['excel_row']}"
            )

        context_fields = [
            mapped.get("product"),
            mapped.get("module"),
            mapped.get("component"),
            mapped.get("trc_occurrence"),
            mapped.get("trc_escape"),
            mapped.get("mrc_occurrence"),
            mapped.get("mrc_escape"),
            mapped.get("cause_level1"),
            mapped.get("cause_level2"),
            mapped.get("cause_level3"),
            mapped.get("cause_level4"),
        ]
        importable = bool(description or itr_values or igr)
        retrieval_ready = bool(description and any(str(value or "").strip() for value in context_fields))
        deep_ready = bool(
            str(mapped.get("failure_mechanism") or "").strip()
            and str(mapped.get("trigger_condition") or "").strip()
        )
        missing = []
        if not description:
            missing.append("issue_fact")
        if not any(str(value or "").strip() for value in context_fields):
            missing.append("retrieval_context")
        if not mapped.get("failure_mechanism"):
            missing.append("failure_mechanism")
        if not mapped.get("trigger_condition"):
            missing.append("trigger_condition")

        return {
            **record,
            "title": title,
            "source_key": source_key,
            "igr": igr,
            "itrs": itr_values,
            "normalized_fields": mapped,
            "completeness": {
                "importable": importable,
                "retrieval_ready": retrieval_ready,
                "deep_analysis_ready": deep_ready,
                "missing_features": missing,
            },
        }

    def preview_excel(
        self,
        excel_path: str | Path,
        *,
        reports_dir: str | Path | None = None,
        group_code: str,
        domain: str = "",
    ) -> dict:
        template = inspect_template(excel_path)
        current_mapping_version = self.current_mapping_version()
        temp_out = self.staging_root / "_parse_preview" / uuid.uuid4().hex
        records, summary = self.excel_parser.parse(excel_path, temp_out)
        enriched = [self._enrich_record(record) for record in records]

        matches: dict[str, dict] = {}
        match_summary = {
            "matched_count": 0,
            "unmatched_count": len(enriched),
            "ambiguous_count": 0,
            "match_type_counts": {},
        }
        if reports_dir and Path(reports_dir).exists():
            match_results, match_summary = self.report_matcher.match_all(
                enriched,
                reports_dir,
                temp_out / "matches",
            )
            matches = {item["case_id"]: item for item in match_results}

        rows = []
        for item in enriched:
            match = matches.get(item["case_id"]) or {
                "match_type": "NO_REPORT_NAME" if not item["normalized_fields"].get("report_filename") else "NOT_FOUND",
                "matched_report_path": "",
                "candidate_paths": [],
                "parse_status": "NO_REPORT" if not item["normalized_fields"].get("report_filename") else "REPORT_NOT_FOUND",
                "parse_warnings": [],
            }
            rows.append({**item, "report_match": match})

        return {
            "group_code": group_code,
            "domain": domain,
            "source_file": Path(excel_path).name,
            "template_contract": template["template_contract"],
            "template_version": template["template_version"],
            "template_status": template["template_status"],
            "mapping_contract": MAPPING_CONTRACT,
            "mapping_version": current_mapping_version,
            "parse_summary": summary.to_dict(),
            "match_summary": match_summary,
            "rows": rows,
            "total": len(rows),
            "importable": sum(1 for row in rows if row["completeness"]["importable"]),
            "retrieval_ready": sum(1 for row in rows if row["completeness"]["retrieval_ready"]),
            "ambiguous": sum(1 for row in rows if row["report_match"]["match_type"] == "AMBIGUOUS"),
        }

    def stage_upload(
        self,
        excel_name: str,
        excel_content: bytes,
        materials: Iterable[tuple[str, bytes]],
        *,
        group_code: str,
        domain: str = "",
        actor: str = "web-user",
    ) -> dict:
        batch_id = f"MIMP-{uuid.uuid4().hex[:16]}"
        root = self.staging_root / batch_id
        reports_dir = root / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        excel_path = root / Path(excel_name or "major_cases.xlsx").name
        excel_path.write_bytes(excel_content)

        used_names: set[str] = set()
        for index, (name, content) in enumerate(materials, 1):
            safe = Path(name or f"material-{index}").name
            candidate = safe
            if candidate in used_names:
                candidate = f"{Path(safe).stem}-{index}{Path(safe).suffix}"
            used_names.add(candidate)
            (reports_dir / candidate).write_bytes(content)

        preview = self.preview_excel(
            excel_path,
            reports_dir=reports_dir,
            group_code=group_code,
            domain=domain,
        )
        preview["batch_id"] = batch_id
        preview["staged_material_count"] = len(used_names)
        actor = str(actor or "").strip() or "web-user"
        preview["governance"] = {
            "contract_version": IMPORT_GOVERNANCE_CONTRACT,
            "template_contract": preview["template_contract"],
            "template_version": preview["template_version"],
            "template_status": preview["template_status"],
            "mapping_contract": preview["mapping_contract"],
            "mapping_version": preview["mapping_version"],
            "preview_actor": actor,
        }
        preview_hash = _hash(preview)
        source_sha256 = hashlib.sha256(excel_content).hexdigest()
        with self.repository.transaction() as connection:
            connection.execute(
                """INSERT INTO kb_major_import_batch(
                     batch_id,source_file,group_code,domain,status,staging_path,preview_json)
                   VALUES(?,?,?,?, 'PREVIEW', ?,?)""",
                (
                    batch_id,
                    excel_path.name,
                    group_code,
                    domain,
                    str(root),
                    _json(preview),
                ),
            )
            connection.execute(
                """INSERT INTO kb_major_import_governance(
                     batch_id,contract_version,template_contract,template_version,
                     template_status,mapping_contract,mapping_version,source_sha256,
                     preview_sha256,preview_actor)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    batch_id,
                    IMPORT_GOVERNANCE_CONTRACT,
                    preview["template_contract"],
                    preview["template_version"],
                    preview["template_status"],
                    preview["mapping_contract"],
                    preview["mapping_version"],
                    source_sha256,
                    preview_hash,
                    actor,
                ),
            )
        return preview

    def batch(self, batch_id: str) -> dict | None:
        with self.repository.connect() as connection:
            row = connection.execute(
                "SELECT * FROM kb_major_import_batch WHERE batch_id=?",
                (batch_id,),
            ).fetchone()
            governance = connection.execute(
                "SELECT * FROM kb_major_import_governance WHERE batch_id=?",
                (batch_id,),
            ).fetchone()
            runs = connection.execute(
                """SELECT * FROM kb_major_import_run
                   WHERE batch_id=? ORDER BY started_at,run_id""",
                (batch_id,),
            ).fetchall()
        if not row:
            return None
        result = dict(row)
        result["preview"] = json.loads(result.pop("preview_json") or "{}")
        result["result"] = json.loads(result.pop("result_json") or "{}")
        result["governance"] = dict(governance) if governance else None
        result["runs"] = []
        for run in runs:
            item = dict(run)
            item["result"] = json.loads(item.pop("result_json") or "{}")
            result["runs"].append(item)
        return result

    def _identity_case(self, group_code: str, identity_type: str, value: str) -> str | None:
        if not value:
            return None
        with self.repository.connect() as connection:
            row = connection.execute(
                """SELECT case_id FROM kb_case_identity
                   WHERE group_code=? AND identity_type=? AND identity_value=?""",
                (group_code, identity_type, value),
            ).fetchone()
        return str(row["case_id"]) if row else None

    def _set_identity(
        self,
        case_id: str,
        group_code: str,
        identity_type: str,
        value: str,
        *,
        primary: bool = False,
    ) -> None:
        value = str(value or "").strip()
        if not value:
            return
        with self.repository.connect() as connection:
            if identity_type == "IGR":
                existing = connection.execute(
                    """SELECT identity_value FROM kb_case_identity
                       WHERE case_id=? AND identity_type='IGR'""",
                    (case_id,),
                ).fetchone()
                if existing and existing["identity_value"] != value:
                    raise ValueError("CASE_IGR_CONFLICT")
            owner = connection.execute(
                """SELECT case_id FROM kb_case_identity
                   WHERE group_code=? AND identity_type=? AND identity_value=?""",
                (group_code, identity_type, value),
            ).fetchone()
            if owner and owner["case_id"] != case_id:
                raise ValueError("CASE_IDENTITY_OWNED_BY_OTHER_CASE")
            connection.execute(
                """INSERT OR IGNORE INTO kb_case_identity(
                     identity_id,case_id,group_code,identity_type,identity_value,is_primary)
                   VALUES(?,?,?,?,?,?)""",
                (_id("KID"), case_id, group_code, identity_type, value, 1 if primary else 0),
            )

    def identities(self, case_id: str) -> list[dict]:
        with self.repository.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM kb_case_identity WHERE case_id=? ORDER BY is_primary DESC,identity_type",
                    (case_id,),
                )
            ]

    def save_source_fact(
        self,
        case_id: str,
        *,
        raw: dict,
        normalized: dict,
        source_ref: str,
        actor: str = "IMPORT",
    ) -> dict:
        """Route Excel into the shared Major Source Fact persistence primitive."""
        return self.repository.add_source_fact_revision(
            case_id,
            source_type="EXCEL",
            source_ref=source_ref,
            raw=raw,
            normalized=normalized,
            actor=actor,
        )

    def source_fact_history(self, case_id: str) -> list[dict]:
        with self.repository.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM kb_source_fact_revision
                   WHERE case_id=? ORDER BY revision_no DESC""",
                (case_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["raw"] = json.loads(item.pop("raw_json") or "{}")
            item["normalized"] = json.loads(item.pop("normalized_json") or "{}")
            result.append(item)
        return result

    def _preflight_commit(self, batch: dict) -> list[dict]:
        preview = batch["preview"]
        rows = list(preview.get("rows") or [])
        errors: list[dict] = []
        if not rows:
            return [{"row": None, "error": "MAJOR_EXCEL_BATCH_EMPTY"}]

        for row in rows:
            row_no = row.get("excel_row")
            if not row.get("completeness", {}).get("importable"):
                errors.append({"row": row_no, "error": "ROW_NOT_IMPORTABLE"})
                continue
            match = row.get("report_match") or {}
            if match.get("match_type") == "AMBIGUOUS":
                errors.append({"row": row_no, "error": "AMBIGUOUS_REPORT_MATCH"})
            matched_path = str(match.get("matched_report_path") or "").strip()
            if matched_path and not Path(matched_path).is_file():
                errors.append({"row": row_no, "error": "MATCHED_REPORT_NOT_FOUND"})

            group_code = str(preview.get("group_code") or "")
            igr = str(row.get("igr") or "")
            source_key = str(row.get("source_key") or "")
            igr_case = self._identity_case(group_code, "IGR", igr)
            source_case = self._identity_case(group_code, "SOURCE_KEY", source_key)
            if igr_case and source_case and igr_case != source_case:
                errors.append({"row": row_no, "error": "CASE_IDENTITY_CONFLICT"})
        return errors

    def _record_failed_run(
        self,
        batch: dict,
        *,
        actor: str,
        errors: list[dict],
        error_code: str,
    ) -> dict:
        run_id = f"MIR-{uuid.uuid4().hex[:16]}"
        result = {
            "batch_id": batch["batch_id"],
            "run_id": run_id,
            "actor": actor,
            "total": len(batch["preview"].get("rows") or []),
            "imported": 0,
            "rejected": len(errors),
            "failed": 0 if errors else 1,
            "errors": errors or [{"error": error_code}],
            "case_ids": [],
            "atomic_rollback": False,
        }
        with self.repository.transaction() as connection:
            connection.execute(
                """INSERT INTO kb_major_import_run(
                     run_id,batch_id,actor,status,imported_count,rejected_count,
                     failed_count,result_json,completed_at)
                   VALUES(?,?,?,'FAILED',0,?,?,?,CURRENT_TIMESTAMP)""",
                (
                    run_id,
                    batch["batch_id"],
                    actor,
                    result["rejected"],
                    result["failed"],
                    _json(result),
                ),
            )
            connection.execute(
                """UPDATE kb_major_import_batch
                   SET status='FAILED',result_json=?,committed_at=CURRENT_TIMESTAMP
                   WHERE batch_id=?""",
                (_json(result), batch["batch_id"]),
            )
        return result

    def _atomic_snapshot(self, batch: dict) -> Path:
        snapshot_root = Path(batch["staging_path"]) / "_atomic_rollback"
        shutil.rmtree(snapshot_root, ignore_errors=True)
        snapshot_root.mkdir(parents=True, exist_ok=True)
        db_backup = snapshot_root / "major.sqlite3"
        with self.repository.connect() as source:
            with sqlite3.connect(db_backup) as target:
                source.backup(target)

        attachment_backup = snapshot_root / "attachments"
        if self.repository.attachment_root.exists():
            shutil.copytree(
                self.repository.attachment_root,
                attachment_backup,
                dirs_exist_ok=True,
            )
        return snapshot_root

    def _restore_atomic_snapshot(self, snapshot_root: Path) -> None:
        db_backup = snapshot_root / "major.sqlite3"
        with sqlite3.connect(db_backup) as source:
            target = self.repository.connect()
            try:
                source.backup(target)
                target.commit()
            finally:
                target.close()

        shutil.rmtree(self.repository.attachment_root, ignore_errors=True)
        attachment_backup = snapshot_root / "attachments"
        if attachment_backup.exists():
            shutil.copytree(
                attachment_backup,
                self.repository.attachment_root,
                dirs_exist_ok=True,
            )
        else:
            self.repository.attachment_root.mkdir(parents=True, exist_ok=True)

    def commit(self, batch_id: str, *, actor: str = "web-user") -> dict:
        """Confirm one governed batch using the existing import implementation."""

        batch = self.batch(batch_id)
        if not batch:
            raise KeyError(batch_id)
        if batch["status"] in {"COMPLETED", "PARTIAL"}:
            return batch["result"]
        if batch["status"] != "PREVIEW":
            raise ValueError("MAJOR_EXCEL_BATCH_NOT_CONFIRMABLE")

        governance = batch.get("governance")
        if not governance:
            self._record_failed_run(
                batch,
                actor=str(actor or "web-user"),
                errors=[],
                error_code="MAJOR_EXCEL_GOVERNANCE_MISSING_REPREVIEW_REQUIRED",
            )
            raise ValueError("MAJOR_EXCEL_GOVERNANCE_MISSING_REPREVIEW_REQUIRED")

        actor = str(actor or "").strip() or str(governance["preview_actor"])
        if self.current_mapping_version() != governance["mapping_version"]:
            self._record_failed_run(
                batch,
                actor=actor,
                errors=[],
                error_code="MAJOR_EXCEL_MAPPING_CHANGED_AFTER_PREVIEW",
            )
            raise ValueError("MAJOR_EXCEL_MAPPING_CHANGED_AFTER_PREVIEW")
        if _hash(batch["preview"]) != governance["preview_sha256"]:
            self._record_failed_run(
                batch,
                actor=actor,
                errors=[],
                error_code="MAJOR_EXCEL_PREVIEW_CHANGED_AFTER_PREVIEW",
            )
            raise ValueError("MAJOR_EXCEL_PREVIEW_CHANGED_AFTER_PREVIEW")

        preflight_errors = self._preflight_commit(batch)
        if preflight_errors:
            self._record_failed_run(
                batch,
                actor=actor,
                errors=preflight_errors,
                error_code="MAJOR_EXCEL_BATCH_PRECHECK_FAILED",
            )
            raise ValueError("MAJOR_EXCEL_BATCH_PRECHECK_FAILED")

        preview = batch["preview"]
        run_id = f"MIR-{uuid.uuid4().hex[:16]}"
        stats = {
            "batch_id": batch_id,
            "run_id": run_id,
            "actor": actor,
            "template_version": governance["template_version"],
            "mapping_version": governance["mapping_version"],
            "total": len(preview.get("rows") or []),
            "imported": 0,
            "rejected": 0,
            "created_cases": 0,
            "reused_cases": 0,
            "source_fact_revisions": 0,
            "events": 0,
            "documents": 0,
            "ambiguous_documents": 0,
            "failed": 0,
            "errors": [],
            "case_ids": [],
            "atomic_rollback": False,
        }
        with self.repository.transaction() as connection:
            connection.execute(
                """INSERT INTO kb_major_import_run(
                     run_id,batch_id,actor,status,result_json)
                   VALUES(?,?,?,'RUNNING','{}')""",
                (run_id, batch_id, actor),
            )
            connection.execute(
                "UPDATE kb_major_import_batch SET status='COMMITTING' WHERE batch_id=?",
                (batch_id,),
            )

        snapshot_root = self._atomic_snapshot(
            {**batch, "status": "COMMITTING"}
        )
        try:
            for row in preview.get("rows") or []:
                group_code = preview["group_code"]
                igr = str(row.get("igr") or "")
                source_key = str(row["source_key"])
                igr_case = self._identity_case(group_code, "IGR", igr)
                source_case = self._identity_case(group_code, "SOURCE_KEY", source_key)
                if igr_case and source_case and igr_case != source_case:
                    raise ValueError("CASE_IDENTITY_CONFLICT")
                case_id = igr_case or source_case
                if case_id:
                    stats["reused_cases"] += 1
                else:
                    case = self.repository.create_case(
                        row["title"],
                        group_code,
                        preview.get("domain") or "",
                        legacy_case_id=source_key,
                    )
                    case_id = case["case_id"]
                    stats["created_cases"] += 1
                stats["case_ids"].append(case_id)
                self._set_identity(
                    case_id,
                    group_code,
                    "SOURCE_KEY",
                    source_key,
                    primary=not bool(igr),
                )
                self._set_identity(
                    case_id,
                    group_code,
                    "IGR",
                    igr,
                    primary=bool(igr),
                )

                source_ref = (
                    f"{preview['source_file']}#"
                    f"{row.get('sheet_name')}:{row.get('excel_row')}"
                )
                source_fact = self.save_source_fact(
                    case_id,
                    raw=row.get("raw_fields") or {},
                    normalized=row.get("normalized_fields") or {},
                    source_ref=source_ref,
                    actor=actor,
                )
                stats["source_fact_revisions"] += 1

                events = []
                for itr in row.get("itrs") or []:
                    event = self.repository.upsert_event(
                        case_id,
                        standard_itr=itr,
                        internal_event_key=itr,
                        title=itr,
                    )
                    events.append(event)
                if not events:
                    events.append(
                        self.repository.upsert_event(
                            case_id,
                            internal_event_key=f"{case_id}:UNLINKED",
                            title=row["title"],
                        )
                    )
                stats["events"] += len(events)

                for event in events:
                    self.repository.add_source_link(
                        case_id,
                        event["event_id"],
                        {
                            "record_id": source_fact["source_fact_revision_id"],
                            "source_type": "MAJOR_EXCEL_SOURCE_FACT",
                            "source_system": "MAJOR_EXCEL_IMPORT",
                            "group_code": group_code,
                            "source_hash": source_fact["source_hash"],
                            "source_ref": source_ref,
                            "batch_id": batch_id,
                            "run_id": run_id,
                            "template_version": governance["template_version"],
                            "mapping_version": governance["mapping_version"],
                        },
                        standard_itr=event.get("standard_itr") or "",
                        role="CURRENT_EVENT",
                        status="LINKED" if event.get("standard_itr") else "NOT_FOUND",
                    )

                match = row.get("report_match") or {}
                if match.get("matched_report_path"):
                    document = self.repository.ingest_file(
                        case_id,
                        match["matched_report_path"],
                        role="PRIMARY",
                    )
                    if document.get("media_type") != "DOC":
                        parsed = parse_document(
                            self.repository.attachment_path(document["version_id"])
                        )
                        self.repository.save_parse_result(
                            document["version_id"],
                            parsed,
                        )
                    primary_event = events[0]
                    self.repository.add_source_link(
                        case_id,
                        primary_event["event_id"],
                        {
                            "record_id": document["version_id"],
                            "source_type": "MAJOR_SOURCE_DOCUMENT",
                            "source_system": "MAJOR_SOURCE_INTAKE",
                            "group_code": group_code,
                            "source_hash": document["content_hash"],
                            "file_name": document["original_filename"],
                            "version_id": document["version_id"],
                            "document_id": document["document_id"],
                            "batch_id": batch_id,
                            "run_id": run_id,
                        },
                        standard_itr=primary_event.get("standard_itr") or "",
                        role="CURRENT_EVENT",
                        status="LINKED" if primary_event.get("standard_itr") else "NOT_FOUND",
                    )
                    stats["documents"] += 1

                self.repository.update_case_status(case_id, "ACTIVE")
                stats["imported"] += 1

            stats["case_ids"] = list(dict.fromkeys(stats["case_ids"]))
            with self.repository.transaction() as connection:
                connection.execute(
                    """UPDATE kb_major_import_batch
                       SET status='COMPLETED',result_json=?,
                           committed_at=CURRENT_TIMESTAMP
                       WHERE batch_id=?""",
                    (_json(stats), batch_id),
                )
                connection.execute(
                    """UPDATE kb_major_import_run
                       SET status='COMPLETED',imported_count=?,rejected_count=0,
                           failed_count=0,result_json=?,completed_at=CURRENT_TIMESTAMP
                       WHERE run_id=?""",
                    (stats["imported"], _json(stats), run_id),
                )
            return stats
        except Exception as exc:
            attempted = {
                key: stats[key]
                for key in (
                    "created_cases",
                    "reused_cases",
                    "source_fact_revisions",
                    "events",
                    "documents",
                    "imported",
                )
            }
            try:
                self._restore_atomic_snapshot(snapshot_root)
            except Exception as rollback_error:
                raise ValueError("MAJOR_EXCEL_ATOMIC_ROLLBACK_FAILED") from rollback_error

            stats.update(
                {
                    "imported": 0,
                    "created_cases": 0,
                    "reused_cases": 0,
                    "source_fact_revisions": 0,
                    "events": 0,
                    "documents": 0,
                    "failed": 1,
                    "case_ids": [],
                    "atomic_rollback": True,
                    "attempted_before_rollback": attempted,
                    "errors": [{"error": str(exc) or type(exc).__name__}],
                }
            )
            with self.repository.transaction() as connection:
                connection.execute(
                    """UPDATE kb_major_import_batch
                       SET status='FAILED',result_json=?,
                           committed_at=CURRENT_TIMESTAMP
                       WHERE batch_id=?""",
                    (_json(stats), batch_id),
                )
                connection.execute(
                    """UPDATE kb_major_import_run
                       SET status='FAILED',imported_count=0,rejected_count=0,
                           failed_count=1,result_json=?,completed_at=CURRENT_TIMESTAMP
                       WHERE run_id=?""",
                    (_json(stats), run_id),
                )
            raise ValueError("MAJOR_EXCEL_ATOMIC_COMMIT_FAILED") from exc
        finally:
            shutil.rmtree(snapshot_root, ignore_errors=True)

    def annotate_revision(
        self,
        revision_id: str,
        *,
        confidence: float | None = None,
        explanation: str = "",
        mechanism: str = "",
        metadata: dict | None = None,
    ) -> None:
        with self.repository.connect() as connection:
            connection.execute(
                """INSERT INTO kb_entry_analysis_meta(
                     revision_id,confidence,explanation,mechanism,metadata_json)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(revision_id) DO UPDATE SET
                     confidence=excluded.confidence,
                     explanation=excluded.explanation,
                     mechanism=excluded.mechanism,
                     metadata_json=excluded.metadata_json""",
                (revision_id, confidence, explanation, mechanism, _json(metadata or {})),
            )

    def _revision_detail(self, connection, revision_id: str) -> dict:
        row = connection.execute(
            """SELECT r.*,m.confidence,m.explanation,m.mechanism,m.metadata_json
               FROM kb_entry_revision r
               LEFT JOIN kb_entry_analysis_meta m ON m.revision_id=r.revision_id
               WHERE r.revision_id=?""",
            (revision_id,),
        ).fetchone()
        item = dict(row) if row else {}
        item["evidence"] = [
            dict(ev)
            for ev in connection.execute(
                "SELECT * FROM kb_evidence WHERE revision_id=? ORDER BY evidence_id",
                (revision_id,),
            )
        ]
        if item.get("metadata_json"):
            item["analysis_metadata"] = json.loads(item["metadata_json"] or "{}")
        return item

    def feature_view(self, case_id: str, event_id: str | None = None) -> dict:
        case = self.repository.get_case(case_id)
        if not case:
            raise KeyError(case_id)
        if event_id:
            event = self.repository.event(event_id)
            if not event or event["case_id"] != case_id:
                raise ValueError("EVENT_CASE_SCOPE_MISMATCH")
            entries = self.repository.entries_for_event(event_id)
        else:
            event = None
            entries = self.repository.entries(case_id)

        facts = self.source_fact_history(case_id)
        latest_fact = facts[0] if facts else None
        source_normalized = dict((latest_fact or {}).get("normalized") or {})
        source_features: dict[str, dict] = {}
        for source_key, feature_key in SOURCE_FEATURE_MAP.items():
            value = source_normalized.get(source_key)
            if value not in (None, "", []):
                source_features[feature_key] = {
                    "field": feature_key,
                    "value": value,
                    "source_layer": "SOURCE_FACT",
                    "revision": (latest_fact or {}).get("revision_no"),
                    "source_ref": (latest_fact or {}).get("source_ref"),
                    "evidence_refs": [],
                    "confidence": None,
                    "review_status": "SOURCE",
                }

        identities = self.identities(case_id)
        igr = next(
            (item["identity_value"] for item in identities if item["identity_type"] == "IGR"),
            "",
        )
        if igr and "igr" not in source_features:
            source_features["igr"] = {
                "field": "igr",
                "value": igr,
                "source_layer": "SOURCE_FACT",
                "revision": None,
                "source_ref": "CASE_IDENTITY",
                "evidence_refs": [],
                "confidence": None,
                "review_status": "SOURCE",
            }

        ai_features: dict[str, dict] = {}
        confirmed_features: dict[str, dict] = {}
        with self.repository.connect() as connection:
            for entry in entries:
                feature_key = ENTRY_FEATURE_MAP.get(entry["entry_type"], entry["entry_type"].lower())
                ai_row = connection.execute(
                    """SELECT revision_id FROM kb_entry_revision
                       WHERE entry_id=? AND origin='AI'
                       ORDER BY revision_no DESC LIMIT 1""",
                    (entry["entry_id"],),
                ).fetchone()
                if ai_row and entry.get("status") not in {"REJECTED", "MISSING"}:
                    rev = self._revision_detail(connection, ai_row["revision_id"])
                    if rev.get("content"):
                        ai_features[feature_key] = {
                            "field": feature_key,
                            "value": rev["content"],
                            "source_layer": "AI_ANALYSIS",
                            "revision": rev.get("revision_no"),
                            "revision_id": rev.get("revision_id"),
                            "evidence_refs": rev.get("evidence") or [],
                            "confidence": rev.get("confidence"),
                            "explanation": rev.get("explanation") or "",
                            "mechanism": rev.get("mechanism") or "",
                            "review_status": entry.get("status"),
                        }
                if entry.get("status") in {"CONFIRMED", "CORRECTED"}:
                    rev = self._revision_detail(connection, entry["current_revision_id"])
                    confirmed_features[feature_key] = {
                        "field": feature_key,
                        "value": rev.get("content") or "",
                        "source_layer": "CONFIRMED",
                        "revision": rev.get("revision_no"),
                        "revision_id": rev.get("revision_id"),
                        "evidence_refs": rev.get("evidence") or [],
                        "confidence": 1.0,
                        "explanation": rev.get("explanation") or "",
                        "mechanism": rev.get("mechanism") or "",
                        "review_status": entry.get("status"),
                    }

        effective: dict[str, dict] = {}
        for key in sorted(set(source_features) | set(ai_features) | set(confirmed_features)):
            candidate = (
                confirmed_features.get(key)
                or ai_features.get(key)
                or source_features.get(key)
            )
            if candidate and candidate.get("value") not in (None, "", []):
                effective[key] = candidate

        issue_fact = str((effective.get("issue_fact") or {}).get("value") or "").strip()
        retrieval_context = [
            "product", "module", "component", "trc_occurrence", "trc_escape",
            "mrc_occurrence", "mrc_escape", "root_cause", "classification_l1",
            "classification_l2", "classification_l3", "classification_l4",
        ]
        retrieval_ready = bool(
            issue_fact
            and any(
                str((effective.get(key) or {}).get("value") or "").strip()
                for key in retrieval_context
            )
        )
        deep_keys = ("root_cause", "failure_mechanism", "trigger_condition")
        deep_values = [
            key for key in deep_keys
            if str((effective.get(key) or {}).get("value") or "").strip()
        ]
        has_deep_evidence = any(
            (effective.get(key) or {}).get("evidence_refs")
            for key in deep_values
        )
        deep_ready = bool(
            ("root_cause" in deep_values or "failure_mechanism" in deep_values)
            and has_deep_evidence
        )
        missing = []
        if not issue_fact:
            missing.append("issue_fact")
        if not retrieval_ready:
            missing.append("retrieval_context")
        for key in deep_keys:
            if key not in effective:
                missing.append(key)

        detail = self.repository.case_detail(case_id) or {}
        documents = [
            {
                "version_id": doc["version_id"],
                "document_id": doc["document_id"],
                "role": doc["document_role"],
                "filename": doc["original_filename"],
                "parse_status": doc["parse_status"],
                "version_no": doc["version_no"],
            }
            for doc in detail.get("documents") or []
        ]
        return {
            "case_identity": {
                "case_id": case_id,
                "igr": igr,
                "event_id": event_id,
                "itrs": [item["standard_itr"] for item in self.repository.events(case_id) if item["standard_itr"]],
                "identities": identities,
            },
            "source_fact": latest_fact,
            "source_fact_history_count": len(facts),
            "ai_analysis": ai_features,
            "confirmed_analysis": confirmed_features,
            "document_evidence": documents,
            "effective_features": effective,
            "completeness": {
                "importable": bool(latest_fact or entries or documents),
                "retrieval_ready": retrieval_ready,
                "deep_analysis_ready": deep_ready,
                "missing_features": list(dict.fromkeys(missing)),
            },
        }
