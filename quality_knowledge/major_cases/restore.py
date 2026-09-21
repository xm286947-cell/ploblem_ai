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
import uuid

from parser.excel_parser import ExcelParser, normalize_header
from parser.report_matcher import ReportMatcher
from quality_knowledge.materials import normalize_itr

from .repository import MajorKnowledgeRepository


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
        self.staging_root = self.repository.attachment_root / "_major_import"
        self.staging_root.mkdir(parents=True, exist_ok=True)

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
        with self.repository.connect() as connection:
            connection.execute(
                """INSERT INTO kb_major_import_batch(
                     batch_id,source_file,group_code,domain,status,staging_path,preview_json)
                   VALUES(?,?,?,?, 'PREVIEW', ?,?)""",
                (
                    batch_id,
                    excel_path.name,
                    group_code,
                    domain,
                    str(root.relative_to(self.repository.attachment_root)),
                    _json(preview),
                ),
            )
        return preview

    def batch(self, batch_id: str) -> dict | None:
        with self.repository.connect() as connection:
            row = connection.execute(
                "SELECT * FROM kb_major_import_batch WHERE batch_id=?",
                (batch_id,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["preview"] = json.loads(result.pop("preview_json") or "{}")
        result["result"] = json.loads(result.pop("result_json") or "{}")
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
        source_hash = _hash({"raw": raw, "normalized": normalized, "source_ref": source_ref})
        with self.repository.transaction() as connection:
            existing = connection.execute(
                """SELECT * FROM kb_source_fact_revision
                   WHERE case_id=? AND source_hash=?""",
                (case_id, source_hash),
            ).fetchone()
            if existing:
                return dict(existing)
            revision_no = int(
                connection.execute(
                    "SELECT COALESCE(MAX(revision_no),0)+1 FROM kb_source_fact_revision WHERE case_id=?",
                    (case_id,),
                ).fetchone()[0]
            )
            revision_id = _id("KSF")
            connection.execute(
                """INSERT INTO kb_source_fact_revision(
                     source_fact_revision_id,case_id,revision_no,source_type,source_ref,
                     source_hash,raw_json,normalized_json,created_by)
                   VALUES(?,?,?,'EXCEL',?,?,?,?,?)""",
                (
                    revision_id,
                    case_id,
                    revision_no,
                    source_ref,
                    source_hash,
                    _json(raw),
                    _json(normalized),
                    actor,
                ),
            )
            row = connection.execute(
                "SELECT * FROM kb_source_fact_revision WHERE source_fact_revision_id=?",
                (revision_id,),
            ).fetchone()
        return dict(row)

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

    def commit(self, batch_id: str, case_service) -> dict:
        batch = self.batch(batch_id)
        if not batch:
            raise KeyError(batch_id)
        if batch["status"] == "COMPLETED":
            return batch["result"]
        preview = batch["preview"]
        stats = {
            "batch_id": batch_id,
            "total": len(preview.get("rows") or []),
            "created_cases": 0,
            "reused_cases": 0,
            "source_fact_revisions": 0,
            "events": 0,
            "documents": 0,
            "ambiguous_documents": 0,
            "failed": 0,
            "errors": [],
            "case_ids": [],
        }
        with self.repository.connect() as connection:
            connection.execute(
                "UPDATE kb_major_import_batch SET status='COMMITTING' WHERE batch_id=?",
                (batch_id,),
            )

        for row in preview.get("rows") or []:
            if not row.get("completeness", {}).get("importable"):
                stats["failed"] += 1
                stats["errors"].append({"row": row.get("excel_row"), "error": "ROW_NOT_IMPORTABLE"})
                continue
            try:
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
                self._set_identity(case_id, group_code, "SOURCE_KEY", source_key, primary=not bool(igr))
                self._set_identity(case_id, group_code, "IGR", igr, primary=bool(igr))

                source_ref = (
                    f"{preview['source_file']}#"
                    f"{row.get('sheet_name')}:{row.get('excel_row')}"
                )
                self.save_source_fact(
                    case_id,
                    raw=row.get("raw_fields") or {},
                    normalized=row.get("normalized_fields") or {},
                    source_ref=source_ref,
                )
                stats["source_fact_revisions"] += 1

                before_events = len(self.repository.events(case_id))
                case_service.associate(case_id, row.get("itrs") or [])
                after_events = len(self.repository.events(case_id))
                stats["events"] += max(0, after_events - before_events)

                match = row.get("report_match") or {}
                if match.get("match_type") == "AMBIGUOUS":
                    stats["ambiguous_documents"] += 1
                elif match.get("matched_report_path"):
                    ingest = case_service.ingest(
                        case_id,
                        match["matched_report_path"],
                        current_itrs=row.get("itrs") or [],
                        role="PRIMARY",
                    )
                    if ingest.get("action") in {"NEW", "UPDATED", "SKIPPED"}:
                        stats["documents"] += 1

                if row.get("completeness", {}).get("retrieval_ready"):
                    self.repository.update_case_status(case_id, "ACTIVE")
            except Exception as exc:
                stats["failed"] += 1
                stats["errors"].append(
                    {
                        "row": row.get("excel_row"),
                        "source_key": row.get("source_key"),
                        "error": str(exc),
                    }
                )

        stats["case_ids"] = list(dict.fromkeys(stats["case_ids"]))
        final = "PARTIAL" if stats["failed"] or stats["ambiguous_documents"] else "COMPLETED"
        with self.repository.connect() as connection:
            connection.execute(
                """UPDATE kb_major_import_batch
                   SET status=?,result_json=?,committed_at=CURRENT_TIMESTAMP
                   WHERE batch_id=?""",
                (final, _json(stats), batch_id),
            )
        return stats

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
