"""Repository for clean P0 operational and analysis data.

The repository never creates tables and never reads a pre-P0 database.  It is
safe to construct only after the initializer has moved the target database to
READY.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Iterable


class P0RepositoryError(RuntimeError):
    """Raised when a caller uses an unready database or breaks P0 invariants."""


class _ClosingSQLiteConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class P0Repository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._assert_ready()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path, timeout=30.0, factory=_ClosingSQLiteConnection
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def save_issue(
        self,
        *,
        knowledge_id: str,
        business_issue_id: str,
        raw_json: dict[str, Any],
        normalized_snapshot: dict[str, Any],
        mapping_config_id: str,
        mapping_config_version: int,
        standard_catalog_version_id: str,
        source_file_sha256: str,
        sheet_name: str,
        row_number: int,
        import_batch_id: str | None = None,
        product_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist immutable raw and normalized issue facts, idempotently."""

        if not knowledge_id or not business_issue_id:
            raise P0RepositoryError("ISSUE_ID_REQUIRED")
        raw_text = self._dump(raw_json)
        normalized_text = self._dump(normalized_snapshot)
        normalized_hash = self._hash(normalized_text)
        source_row_hash = self._hash(raw_text)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT issue_version_id FROM quality_issue_version
                   WHERE knowledge_id = ? AND normalized_hash = ?""",
                (knowledge_id, normalized_hash),
            ).fetchone()
            if existing:
                connection.commit()
                return {"outcome": "ALREADY_APPLIED", "issue_version_id": existing["issue_version_id"]}
            issue = connection.execute(
                "SELECT current_version_id FROM quality_issue WHERE knowledge_id = ?", (knowledge_id,)
            ).fetchone()
            if issue is None:
                connection.execute(
                    """INSERT INTO quality_issue(knowledge_id, business_issue_id, product_id)
                       VALUES (?, ?, ?)""",
                    (knowledge_id, business_issue_id, product_id),
                )
                version_no = 1
            else:
                version_no = connection.execute(
                    "SELECT COALESCE(MAX(version_no), 0) + 1 FROM quality_issue_version WHERE knowledge_id = ?",
                    (knowledge_id,),
                ).fetchone()[0]
            issue_version_id = f"QIV-{uuid.uuid4().hex}"
            connection.execute(
                """INSERT INTO quality_issue_version(
                        issue_version_id, knowledge_id, version_no, mapping_config_id,
                        mapping_config_version, standard_catalog_version_id, normalized_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    issue_version_id,
                    knowledge_id,
                    version_no,
                    mapping_config_id,
                    mapping_config_version,
                    standard_catalog_version_id,
                    normalized_hash,
                ),
            )
            connection.execute(
                """INSERT INTO issue_source_raw(
                        source_raw_id, issue_version_id, import_batch_id, source_file_sha256,
                        sheet_name, row_number, source_row_hash, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"ISR-{uuid.uuid4().hex}",
                    issue_version_id,
                    import_batch_id,
                    source_file_sha256,
                    sheet_name,
                    row_number,
                    source_row_hash,
                    raw_text,
                ),
            )
            connection.execute(
                """INSERT INTO issue_normalized_snapshot(
                        normalized_snapshot_id, issue_version_id, snapshot_json, snapshot_hash
                    ) VALUES (?, ?, ?, ?)""",
                (f"INS-{uuid.uuid4().hex}", issue_version_id, normalized_text, normalized_hash),
            )
            connection.execute(
                """UPDATE quality_issue
                      SET current_version_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE knowledge_id = ?""",
                (issue_version_id, knowledge_id),
            )
            connection.commit()
        return {"outcome": "APPLIED", "issue_version_id": issue_version_id, "version_no": version_no}

    def get_issue(self, knowledge_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT issue.*, version.issue_version_id, version.version_no,
                       version.mapping_config_id, version.mapping_config_version,
                       version.standard_catalog_version_id, version.normalized_hash,
                       snapshot.snapshot_json, source.raw_json
                  FROM quality_issue AS issue
                  LEFT JOIN quality_issue_version AS version
                    ON version.issue_version_id = issue.current_version_id
                  LEFT JOIN issue_normalized_snapshot AS snapshot
                    ON snapshot.issue_version_id = version.issue_version_id
                  LEFT JOIN issue_source_raw AS source
                    ON source.issue_version_id = version.issue_version_id
                 WHERE issue.knowledge_id = ?
                """,
                (knowledge_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["normalized_snapshot"] = json.loads(result.pop("snapshot_json") or "{}")
        result["raw_json"] = json.loads(result["raw_json"] or "{}")
        return result

    def get_analysis_set_by_input_hash(
        self, issue_version_id: str, input_hash: str
    ) -> dict[str, Any] | None:
        """Get a previous immutable V2 analysis for an exact frozen input."""

        with self.connect() as connection:
            row = connection.execute(
                """SELECT analysis_set_id FROM analysis_set
                     WHERE issue_version_id = ? AND input_hash = ?""",
                (issue_version_id, input_hash),
            ).fetchone()
        return self.get_analysis_set(row["analysis_set_id"]) if row else None

    def get_latest_analysis_set(self, knowledge_id: str) -> dict[str, Any] | None:
        """Get the newest V2 analysis for an issue; never reads historical JSON."""

        with self.connect() as connection:
            row = connection.execute(
                """SELECT analysis_set_id FROM analysis_set
                     WHERE knowledge_id = ?
                     ORDER BY rowid DESC LIMIT 1""",
                (knowledge_id,),
            ).fetchone()
        return self.get_analysis_set(row["analysis_set_id"]) if row else None

    def get_active_prompt(self, stage: str) -> dict[str, Any]:
        """Return one immutable ACTIVE native V2 prompt from the P0 database."""

        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM analysis_prompt_version
                     WHERE stage = ? AND status = 'ACTIVE'""",
                (stage,),
            ).fetchone()
        if row is None:
            raise P0RepositoryError(f"ACTIVE_PROMPT_NOT_FOUND:{stage}")
        return self._decode_json_columns(dict(row))

    def get_active_scoring_version(self) -> dict[str, Any]:
        """Return the frozen active seven-component scoring version."""

        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM insight_scoring_version WHERE status = 'ACTIVE'"
            ).fetchone()
        if row is None:
            raise P0RepositoryError("ACTIVE_INSIGHT_SCORING_VERSION_NOT_FOUND")
        return self._decode_json_columns(dict(row))

    def save_analysis_set(self, analysis: dict[str, Any]) -> dict[str, Any]:
        """Save the immutable AI Analysis Set and its structured projections."""

        required = {"analysis_set_id", "knowledge_id", "issue_version_id", "taxonomy_version_id", "input_hash"}
        missing = sorted(key for key in required if not analysis.get(key))
        if missing:
            raise P0RepositoryError(f"ANALYSIS_SET_FIELDS_REQUIRED:{','.join(missing)}")
        status = analysis.get("status", "COMPLETED")
        if status not in {"COMPLETED", "PARTIAL_FAILED", "FAILED"}:
            raise P0RepositoryError("ANALYSIS_SET_STATUS_INVALID")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT analysis_set_id FROM analysis_set WHERE issue_version_id = ? AND input_hash = ?",
                (analysis["issue_version_id"], analysis["input_hash"]),
            ).fetchone()
            if existing and existing["analysis_set_id"] != analysis["analysis_set_id"]:
                connection.commit()
                return {"outcome": "ALREADY_APPLIED", "analysis_set_id": existing["analysis_set_id"]}
            connection.execute(
                """
                INSERT INTO analysis_set(
                    analysis_set_id, knowledge_id, issue_version_id, status,
                    contract_version, taxonomy_version_id,
                    classification_mapping_versions_json, prompt_versions_json,
                    scoring_version_id, analysis_profile_json, source_coverage_json,
                    classification_consistency, input_hash, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(analysis_set_id) DO UPDATE SET
                    status = excluded.status,
                    classification_mapping_versions_json = excluded.classification_mapping_versions_json,
                    prompt_versions_json = excluded.prompt_versions_json,
                    scoring_version_id = excluded.scoring_version_id,
                    analysis_profile_json = excluded.analysis_profile_json,
                    source_coverage_json = excluded.source_coverage_json,
                    classification_consistency = excluded.classification_consistency,
                    completed_at = excluded.completed_at
                """,
                (
                    analysis["analysis_set_id"],
                    analysis["knowledge_id"],
                    analysis["issue_version_id"],
                    status,
                    analysis.get("contract_version", "2.0.0"),
                    analysis["taxonomy_version_id"],
                    self._dump(analysis.get("classification_mapping_versions", {})),
                    self._dump(analysis.get("prompt_versions", {})),
                    analysis.get("scoring_version_id"),
                    self._dump(analysis.get("analysis_profile", {})),
                    self._dump(analysis.get("source_coverage", {})),
                    analysis.get("classification_consistency"),
                    analysis["input_hash"],
                ),
            )
            self._replace_analysis_projections(connection, analysis)
            connection.commit()
        return {"outcome": "APPLIED", "analysis_set_id": analysis["analysis_set_id"], "status": status}

    def get_analysis_set(self, analysis_set_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            record = connection.execute(
                "SELECT * FROM analysis_set WHERE analysis_set_id = ?", (analysis_set_id,)
            ).fetchone()
            if record is None:
                return None
            result = self._decode_json_columns(dict(record))
            result["stage_runs"] = [
                self._decode_json_columns(dict(row))
                for row in connection.execute(
                    "SELECT * FROM analysis_stage_run WHERE analysis_set_id = ? ORDER BY stage",
                    (analysis_set_id,),
                )
            ]
            result["tags"] = [dict(row) for row in connection.execute(
                "SELECT * FROM issue_analysis_tag WHERE analysis_set_id = ? ORDER BY stage, axis, tag_code",
                (analysis_set_id,),
            )]
            result["mrc"] = [dict(row) for row in connection.execute(
                "SELECT * FROM issue_mrc WHERE analysis_set_id = ? ORDER BY side, role", (analysis_set_id,)
            )]
            result["capability_gaps"] = [
                self._decode_json_columns(dict(row)) for row in connection.execute(
                    "SELECT * FROM issue_capability_gap WHERE analysis_set_id = ? ORDER BY capability_axis, capability_code",
                    (analysis_set_id,),
                )
            ]
            result["values"] = [
                self._decode_json_columns(dict(row)) for row in connection.execute(
                    """SELECT * FROM issue_analysis_value WHERE analysis_set_id = ?
                         ORDER BY stage, value_path""",
                    (analysis_set_id,),
                )
            ]
            result["open_questions"] = [
                self._decode_json_columns(dict(row)) for row in connection.execute(
                    """SELECT * FROM analysis_open_question WHERE analysis_set_id = ?
                         ORDER BY stage, question_key""",
                    (analysis_set_id,),
                )
            ]
            result["evidence"] = [
                dict(row) for row in connection.execute(
                    """SELECT * FROM analysis_evidence WHERE analysis_set_id = ?
                         ORDER BY stage, target_path, evidence_id""",
                    (analysis_set_id,),
                )
            ]
            return result

    def save_human_revision(
        self,
        *,
        base_analysis_set_id: str,
        base_input_hash: str,
        confirmed_by: str,
        answers: Iterable[dict[str, Any]],
        expected_revision_no: int | None = None,
    ) -> dict[str, Any]:
        """Create an auditable human revision without changing AI/raw records."""

        if not confirmed_by:
            raise P0RepositoryError("HUMAN_CONFIRMATION_ACTOR_REQUIRED")
        answers = list(answers)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            analysis = connection.execute(
                "SELECT input_hash, knowledge_id, issue_version_id FROM analysis_set WHERE analysis_set_id = ?",
                (base_analysis_set_id,),
            ).fetchone()
            if analysis is None:
                raise P0RepositoryError("ANALYSIS_SET_NOT_FOUND")
            if analysis["input_hash"] != base_input_hash:
                raise P0RepositoryError("HUMAN_CONFIRMATION_STALE")
            current_issue = connection.execute(
                "SELECT current_version_id FROM quality_issue WHERE knowledge_id = ?",
                (analysis["knowledge_id"],),
            ).fetchone()
            if current_issue is None or current_issue["current_version_id"] != analysis["issue_version_id"]:
                raise P0RepositoryError("HUMAN_CONFIRMATION_STALE")
            latest_revision = connection.execute(
                "SELECT COALESCE(MAX(revision_no), 0) FROM human_analysis_revision WHERE base_analysis_set_id = ?",
                (base_analysis_set_id,),
            ).fetchone()[0]
            if expected_revision_no is not None and expected_revision_no != latest_revision:
                raise P0RepositoryError("HUMAN_CONFIRMATION_VERSION_CONFLICT")
            revision_no = latest_revision + 1
            revision_id = f"HAR-{uuid.uuid4().hex}"
            connection.execute(
                """
                INSERT INTO human_analysis_revision(
                    human_revision_id, base_analysis_set_id, revision_no, status,
                    confirmed_by, base_input_hash, confirmed_at
                ) VALUES (?, ?, ?, 'CONFIRMED', ?, ?, CURRENT_TIMESTAMP)
                """,
                (revision_id, base_analysis_set_id, revision_no, confirmed_by, base_input_hash),
            )
            seen_paths: set[str] = set()
            for answer in answers:
                path = str(answer.get("target_path") or "")
                if not path or path in seen_paths:
                    raise P0RepositoryError("HUMAN_CONFIRMATION_TARGET_PATH_INVALID")
                seen_paths.add(path)
                status = str(answer.get("confirmation_status") or "CONFIRMED").upper()
                if status not in {"PENDING", "CONFIRMED", "CORRECTED", "UNRESOLVED", "NOT_APPLICABLE"}:
                    raise P0RepositoryError("HUMAN_CONFIRMATION_STATUS_INVALID")
                confirmed_value = answer.get("confirmed_value")
                if status in {"CONFIRMED", "CORRECTED"} and confirmed_value in (None, ""):
                    raise P0RepositoryError("HUMAN_CONFIRMATION_VALUE_REQUIRED")
                changes_insight = bool(answer.get("changes_insight")) and status in {"CONFIRMED", "CORRECTED"}
                connection.execute(
                    """
                    INSERT INTO human_analysis_answer(
                        human_answer_id, human_revision_id, target_path, question_key, confirmation_status,
                        original_value_json, confirmed_value_json, evidence_json, changes_insight
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"HAA-{uuid.uuid4().hex}",
                        revision_id,
                        path,
                        str(answer.get("question_key") or ""),
                        status,
                        self._dump(answer.get("original_value")),
                        self._dump(confirmed_value),
                        self._dump(answer.get("evidence", [])),
                        int(changes_insight),
                    ),
                )
            connection.execute(
                """INSERT INTO human_confirmation_audit(
                        confirmation_audit_id, human_revision_id, action, actor, details_json
                    ) VALUES (?, ?, 'CONFIRMED', ?, ?)""",
                (f"HCA-{uuid.uuid4().hex}", revision_id, confirmed_by, self._dump({"answer_count": len(answers)})),
            )
            connection.commit()
        return {"human_revision_id": revision_id, "revision_no": revision_no, "outcome": "APPLIED"}

    def get_human_revisions(self, base_analysis_set_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            revisions = [dict(row) for row in connection.execute(
                """SELECT * FROM human_analysis_revision
                     WHERE base_analysis_set_id = ? ORDER BY revision_no DESC""",
                (base_analysis_set_id,),
            )]
            for revision in revisions:
                revision["answers"] = [self._decode_json_columns(dict(row)) for row in connection.execute(
                    "SELECT * FROM human_analysis_answer WHERE human_revision_id = ? ORDER BY target_path",
                    (revision["human_revision_id"],),
                )]
            return revisions

    def get_effective_analysis(self, analysis_set_id: str) -> dict[str, Any]:
        """Resolve the latest confirmed human values over immutable AI values.

        This is a read model only: neither raw source rows nor AI projections are
        updated when a person confirms a correction.
        """

        analysis = self.get_analysis_set(analysis_set_id)
        if analysis is None:
            raise P0RepositoryError("V2_ANALYSIS_NOT_AVAILABLE")
        effective: dict[str, Any] = {
            row["value_path"]: row["value_json"] for row in analysis["values"]
        }
        for mrc in analysis["mrc"]:
            side = mrc["side"].lower()
            if mrc["role"] == "PRIMARY":
                effective[f"{side}.mrc.primary"] = mrc["mrc_code"]
        for gap in analysis["capability_gaps"]:
            path = (
                f"capability_gaps.{gap['capability_axis']}."
                f"{gap['capability_code']}.{gap['governance_scope']}"
            )
            effective[path] = gap["details_json"]

        revisions = self.get_human_revisions(analysis_set_id)
        if revisions:
            for answer in revisions[0]["answers"]:
                if answer.get("confirmation_status", "CONFIRMED") in {"CONFIRMED", "CORRECTED"}:
                    effective[answer["target_path"]] = answer["confirmed_value_json"]
        return {
            "analysis_set_id": analysis_set_id,
            "base_input_hash": analysis["input_hash"],
            "values": effective,
            "human_revision_id": revisions[0]["human_revision_id"] if revisions else None,
        }

    def _replace_analysis_projections(self, connection: sqlite3.Connection, analysis: dict[str, Any]) -> None:
        analysis_set_id = analysis["analysis_set_id"]
        for table in (
            "analysis_stage_run",
            "issue_analysis_value",
            "issue_analysis_tag",
            "issue_mrc",
            "issue_capability_gap",
            "analysis_open_question",
            "analysis_evidence",
        ):
            connection.execute(f"DELETE FROM {table} WHERE analysis_set_id = ?", (analysis_set_id,))
        for stage in analysis.get("stages", []):
            connection.execute(
                """INSERT INTO analysis_stage_run(
                        analysis_stage_run_id, analysis_set_id, stage, status, input_hash,
                        model_name, prompt_version_id, raw_response, parsed_result_json,
                        validation_error, debug_json, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (
                    stage.get("analysis_stage_run_id") or f"ASR-{uuid.uuid4().hex}",
                    analysis_set_id,
                    stage["stage"],
                    stage.get("status", "COMPLETED"),
                    stage.get("input_hash", analysis["input_hash"]),
                    stage.get("model_name"),
                    stage.get("prompt_version_id"),
                    stage.get("raw_response"),
                    self._dump(stage.get("parsed_result")),
                    stage.get("validation_error"),
                    self._dump(stage.get("debug", {})),
                ),
            )
        for value in analysis.get("values", []):
            self._assert_source_confidence(value)
            connection.execute(
                """INSERT INTO issue_analysis_value(
                        analysis_value_id, analysis_set_id, stage, value_path,
                        value_json, source_type, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    value.get("analysis_value_id") or f"AIV-{uuid.uuid4().hex}",
                    analysis_set_id,
                    value["stage"],
                    value["value_path"],
                    self._dump(value.get("value")),
                    value.get("source_type", "AI_INFERRED"),
                    float(value.get("confidence", 0)),
                ),
            )
        for tag in analysis.get("tags", []):
            self._assert_source_confidence(tag)
            connection.execute(
                """INSERT INTO issue_analysis_tag(
                        analysis_tag_id, analysis_set_id, stage, axis, tag_code,
                        tag_role, source_type, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tag.get("analysis_tag_id") or f"IAT-{uuid.uuid4().hex}",
                    analysis_set_id,
                    tag["stage"],
                    tag["axis"],
                    tag["tag_code"],
                    tag.get("tag_role", "SECONDARY"),
                    tag.get("source_type", "AI_INFERRED"),
                    float(tag.get("confidence", 0)),
                ),
            )
        for mrc in analysis.get("mrc", []):
            self._assert_source_confidence(mrc)
            connection.execute(
                """INSERT INTO issue_mrc(
                        issue_mrc_id, analysis_set_id, side, mrc_code, role,
                        control_status, source_type, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    mrc.get("issue_mrc_id") or f"MRC-{uuid.uuid4().hex}",
                    analysis_set_id,
                    mrc["side"],
                    mrc["mrc_code"],
                    mrc.get("role", "PRIMARY"),
                    mrc.get("control_status", "UNKNOWN"),
                    mrc.get("source_type", "AI_INFERRED"),
                    float(mrc.get("confidence", 0)),
                ),
            )
        for gap in analysis.get("capability_gaps", []):
            self._assert_source_confidence(gap)
            connection.execute(
                """INSERT INTO issue_capability_gap(
                        issue_capability_gap_id, analysis_set_id, capability_axis,
                        capability_code, governance_scope, control_status, details_json,
                        source_type, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    gap.get("issue_capability_gap_id") or f"ICG-{uuid.uuid4().hex}",
                    analysis_set_id,
                    gap["capability_axis"],
                    gap["capability_code"],
                    gap.get("governance_scope", "PRODUCT"),
                    gap.get("control_status", "UNKNOWN"),
                    self._dump(gap.get("details", {})),
                    gap.get("source_type", "AI_INFERRED"),
                    float(gap.get("confidence", 0)),
                ),
            )
        for question in analysis.get("open_questions", []):
            connection.execute(
                """INSERT INTO analysis_open_question(
                        open_question_id, analysis_set_id, stage, target_path,
                        question_key, question_text, priority, options_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    question.get("open_question_id") or f"AOQ-{uuid.uuid4().hex}",
                    analysis_set_id,
                    question["stage"],
                    question.get("target_path", ""),
                    question["question_key"],
                    question["question_text"],
                    question.get("priority", "MEDIUM"),
                    self._dump(question.get("options", [])),
                ),
            )
        for evidence in analysis.get("evidence", []):
            self._assert_source_confidence(evidence)
            connection.execute(
                """INSERT INTO analysis_evidence(
                        evidence_id, analysis_set_id, stage, target_path, source_type,
                        source_ref, field_path, excerpt, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    evidence.get("evidence_id") or f"AE-{uuid.uuid4().hex}",
                    analysis_set_id,
                    evidence["stage"],
                    evidence.get("target_path", ""),
                    evidence.get("source_type", "AI_INFERRED"),
                    evidence["source_ref"],
                    evidence.get("field_path"),
                    evidence.get("excerpt", ""),
                    float(evidence.get("confidence", 0)),
                ),
            )

    @staticmethod
    def _assert_source_confidence(value: dict[str, Any]) -> None:
        source_type = value.get("source_type", "AI_INFERRED")
        confidence = float(value.get("confidence", 0))
        if source_type == "AI_INFERRED" and confidence > 0.60:
            raise P0RepositoryError("AI_INFERRED_CONFIDENCE_EXCEEDS_LIMIT")

    def _assert_ready(self) -> None:
        if not self.db_path.exists():
            raise P0RepositoryError("P0_DATABASE_NOT_INITIALIZED")
        try:
            with self.connect() as connection:
                row = connection.execute(
                    "SELECT initialization_state FROM system_database_metadata LIMIT 1"
                ).fetchone()
        except sqlite3.Error as error:
            raise P0RepositoryError("P0_DATABASE_NOT_INITIALIZED") from error
        if row is None or row["initialization_state"] != "READY":
            raise P0RepositoryError("P0_DATABASE_NOT_READY")

    @staticmethod
    def _dump(value: Any) -> str:
        return json.dumps(value if value is not None else None, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _decode_json_columns(row: dict[str, Any]) -> dict[str, Any]:
        for key in list(row):
            if key.endswith("_json") and row[key] is not None:
                row[key] = json.loads(row[key])
        return row
