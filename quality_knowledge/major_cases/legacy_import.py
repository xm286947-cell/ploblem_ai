"""Preview-first, idempotent import of legacy Standard Case JSON."""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import uuid

from builder.validators import validate_json
from .repository import MajorKnowledgeRepository


def _flatten_values(value) -> str:
    values: list[str] = []
    if isinstance(value, dict):
        preferred = value.get("value") or value.get("effective") or value.get("standard")
        if preferred:
            values.append(str(preferred))
        else:
            for child in value.values():
                text = _flatten_values(child)
                if text:
                    values.append(text)
    elif isinstance(value, list):
        for child in value:
            text = _flatten_values(child)
            if text:
                values.append(text)
    elif value not in (None, ""):
        values.append(str(value))
    return "\n".join(dict.fromkeys(values))


class LegacyCaseImporter:
    def __init__(self, repository: MajorKnowledgeRepository, schema_path: str | Path):
        self.repository = repository
        self.schema_path = Path(schema_path)

    def preview(self, source_path: str | Path, group_code: str) -> dict:
        path = Path(source_path)
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw)
        errors = validate_json(data, self.schema_path)
        metadata = data.get("metadata") or {}
        legacy_case_id = str(metadata.get("case_id") or path.stem)
        preview = {
            "legacy_case_id": legacy_case_id,
            "source_hash": digest,
            "group_code": group_code,
            "schema_valid": not errors,
            "errors": errors,
            "itr_id": str(metadata.get("itr_id") or ""),
            "title": _flatten_values(data.get("problem", {}).get("problem_summary")) or legacy_case_id,
            "entry_types": {
                "ISSUE_FACT": bool(_flatten_values(data.get("problem"))),
                "ROOT_CAUSE": bool(_flatten_values(data.get("analysis"))),
                "ACTION": bool(_flatten_values(data.get("solution"))),
            },
        }
        with self.repository.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM kb_legacy_import WHERE legacy_case_id=? AND source_hash=?", (legacy_case_id, digest)
            ).fetchone()
            if existing:
                return {**preview, "import_id": existing["import_id"], "state": existing["state"], "reused": True}
            import_id = f"KIMPORT-{uuid.uuid4().hex}"
            connection.execute(
                "INSERT INTO kb_legacy_import(import_id,legacy_case_id,source_hash,preview_json) VALUES(?,?,?,?)",
                (import_id, legacy_case_id, digest, json.dumps({**preview, "source_path": str(path)}, ensure_ascii=False)),
            )
        return {**preview, "import_id": import_id, "state": "PREVIEW", "reused": False}

    def confirm(self, import_id: str, *, reviewer: str) -> dict:
        with self.repository.connect() as connection:
            row = connection.execute("SELECT * FROM kb_legacy_import WHERE import_id=?", (import_id,)).fetchone()
        if not row:
            raise KeyError(import_id)
        if row["state"] == "IMPORTED":
            return {"import_id": import_id, "case_id": row["imported_case_id"], "state": "IMPORTED", "reused": True}
        preview = json.loads(row["preview_json"])
        if not preview["schema_valid"]:
            raise ValueError("LEGACY_SCHEMA_INVALID")
        data = json.loads(Path(preview["source_path"]).read_text(encoding="utf-8"))
        case = self.repository.create_case(preview["title"], preview["group_code"], legacy_case_id=preview["legacy_case_id"])
        metadata = data.get("metadata") or {}
        self.repository.upsert_event(case["case_id"], standard_itr=str(metadata.get("itr_id") or ""), internal_event_key=str(metadata.get("itr_id") or preview["legacy_case_id"]), title=preview["title"])
        mappings = {
            "ISSUE_FACT": data.get("problem"),
            "ROOT_CAUSE": data.get("analysis"),
            "ACTION": data.get("solution"),
        }
        for entry_type, value in mappings.items():
            content = _flatten_values(value)
            if content:
                self.repository.add_entry(
                    case["case_id"], entry_type, content, assertion_kind="AI_INFERENCE", origin="LEGACY_IMPORT",
                    status="PENDING", model_profile=str(metadata.get("model_version") or "legacy-unknown"),
                )
        with self.repository.connect() as connection:
            connection.execute(
                "UPDATE kb_legacy_import SET imported_case_id=?,state='IMPORTED' WHERE import_id=?",
                (case["case_id"], import_id),
            )
            connection.execute(
                "INSERT INTO kb_review(review_id,target_type,target_id,action,after_json,reviewer) VALUES(?,?,?,?,?,?)",
                (f"KREVIEW-{uuid.uuid4().hex}", "LEGACY_IMPORT", import_id, "CONFIRM_IMPORT", json.dumps({"case_id": case["case_id"]}), reviewer),
            )
        return {"import_id": import_id, "case_id": case["case_id"], "state": "IMPORTED", "reused": False}
