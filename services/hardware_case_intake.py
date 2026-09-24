"""Company-local Word intake lifecycle over the existing Case and Runtime adapters."""
from __future__ import annotations

import sqlite3
import re
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from services.hardware_case_ai_adapter import HardwareCaseAIAdapter, derive_identity
from services.hardware_case_backend import HardwareCaseBackendService
from services.hardware_case_source_store import HardwareCaseSourceStore
from services.hardware_case_word import HardwareWordParseError, parse_docx


SCHEMA = """
CREATE TABLE IF NOT EXISTS hardware_case_intake (
    intake_id TEXT PRIMARY KEY,
    source_ref TEXT NOT NULL UNIQUE,
    source_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    status TEXT NOT NULL,
    case_id TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _tree_candidates(document: dict[str, Any], nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ground independent tree proposals in literal leaf names and Word blocks."""
    candidates = []
    for node in nodes:
        if not node.get("active") or not node.get("name"):
            continue
        name = str(node["name"])
        pattern = re.compile(r"(?<![A-Za-z])" + re.escape(name) + r"(?![A-Za-z])" if name.isascii() else re.escape(name))
        longer = [str(other["name"]) for other in nodes if other.get("active") and other.get("name") != name and name in str(other.get("name") or "")]
        matches = []
        for block in document.get("blocks") or []:
            if block.get("block_type") not in {"PARAGRAPH", "TABLE"}:
                continue
            text = str(block.get("text") or "")
            covered = [match.span() for term in longer for match in re.finditer(re.escape(term), text)]
            if any(not any(start >= left and end <= right for left, right in covered)
                   for start, end in (match.span() for match in pattern.finditer(text))):
                matches.append(block["block_id"])
        if matches:
            candidates.append({"node_id": node["node_id"], "node_path": node["path"],
                               "confidence": 0.8, "evidence_block_ids": matches})
    return candidates


class HardwareCaseIntakeError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class HardwareCaseIntakeService:
    def __init__(
        self,
        database: str | Path,
        sources: HardwareCaseSourceStore,
        backend: HardwareCaseBackendService,
        structurer_factory: Callable[[], Callable[[dict[str, Any]], dict[str, Any]]],
    ):
        self.database = Path(database)
        self.sources = sources
        self.backend = backend
        self.structurer_factory = structurer_factory
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def get(self, intake_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM hardware_case_intake WHERE intake_id=?", (intake_id,)).fetchone()
        if row is None:
            raise HardwareCaseIntakeError("INTAKE_NOT_FOUND")
        return self._public(row)

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM hardware_case_intake ORDER BY created_at DESC, intake_id DESC").fetchall()
        return [self._public(row) for row in rows]

    def _update(self, intake_id: str, status: str, *, case_id: str | None = None, error_code: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE hardware_case_intake SET status=?,case_id=COALESCE(?,case_id),error_code=?,updated_at=CURRENT_TIMESTAMP WHERE intake_id=?",
                (status, case_id, error_code, intake_id),
            )

    def upload(self, filename: str, content: bytes) -> dict[str, Any]:
        if (not filename or Path(filename).name != filename or "\\" in filename
                or any(ord(char) < 32 for char in filename) or not filename.lower().endswith(".docx")):
            raise HardwareCaseIntakeError("DOCX_ONLY")
        source_ref = f"word:{filename}"
        metadata = self.sources.register_bytes(source_ref, filename, content)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM hardware_case_intake WHERE source_ref=?", (source_ref,)).fetchone()
            if row is not None:
                return self._public(row)  # exact same source; no duplicate task
            intake_id = "HCI-" + uuid4().hex.upper()
            connection.execute(
                "INSERT INTO hardware_case_intake(intake_id,source_ref,source_id,filename,status) VALUES(?,?,?,?,'UPLOADED')",
                (intake_id, source_ref, metadata["source_id"], filename),
            )
        return self.get(intake_id)

    def process(self, intake_id: str) -> dict[str, Any]:
        item = self.get(intake_id)
        if item["status"] in {"CANDIDATE_READY", "NEEDS_REVIEW"}:
            raise HardwareCaseIntakeError("INTAKE_ALREADY_PROCESSED")
        if item["status"] not in {"UPLOADED", "FAILED"}:
            raise HardwareCaseIntakeError("INTAKE_PROCESSING")
        # The two independent ACTIVE trees are a prerequisite for this import path.
        for tree_type in ("CIRCUIT_FEATURE", "MATERIAL_DEVICE"):
            if not self.backend.repository.get_active_tree_version_id(tree_type):
                raise HardwareCaseIntakeError("ACTIVE_TREES_REQUIRED")
        try:
            path = self.sources.resolve_path(item["source_ref"])
            parsed = parse_docx(path)
            case_id, _ = derive_identity(parsed)
            existing = self.backend.repository.get_case(case_id)
            if existing and existing.get("processing_status") != "STRUCTURE_EXTRACTION_FAILED":
                raise HardwareCaseIntakeError("CASE_ALREADY_EXISTS")
            self._update(intake_id, "PARSED", case_id=case_id)
            self._update(intake_id, "AI_PROCESSING")
            base_structurer = self.structurer_factory()

            def grounded_structurer(document: dict[str, Any]) -> dict[str, Any]:
                current = self.backend.repository.list_tree_nodes()
                circuit = _tree_candidates(document, [node for node in current if node["tree_type"] == "CIRCUIT_FEATURE"])
                material = _tree_candidates(document, [node for node in current if node["tree_type"] == "MATERIAL_DEVICE"])
                result = base_structurer({**document, "tree_candidates": {
                    "circuit_feature_links": circuit, "material_links": material,
                }})
                if not isinstance(result, dict):
                    return result
                result = dict(result)
                for key, proposals in (("circuit_feature_links", circuit), ("material_links", material)):
                    allowed = {item["node_id"]: item for item in proposals}
                    selected = [item for item in result.get(key, []) if isinstance(item, dict)
                                and item.get("node_id") in allowed
                                and set(item.get("evidence_block_ids") or []) & set(allowed[item["node_id"]]["evidence_block_ids"])]
                    result[key] = selected or proposals
                return result

            adapter = HardwareCaseAIAdapter(self.backend, grounded_structurer, source_store=self.sources)
            result = adapter.ingest_docx(path)
            status = "FAILED" if result["status"] == "FAILED" else "NEEDS_REVIEW" if result["status"] == "NEEDS_REVIEW" else "CANDIDATE_READY"
            if status == "CANDIDATE_READY":
                facts = self.backend.repository.get_case(case_id).get("facts", {})
                if any(not facts.get(key, {}).get("candidate_value") for key in ("symptom", "root_cause", "actions")):
                    status = "NEEDS_REVIEW"
            self._update(intake_id, status, case_id=case_id, error_code=(result.get("warnings") or [None])[0] if status == "FAILED" else None)
        except HardwareCaseIntakeError:
            raise
        except Exception as error:
            # Never persist exception messages: they can include source text or paths.
            code = error.code if isinstance(error, HardwareWordParseError) else "INTAKE_PROCESS_FAILED"
            self._update(intake_id, "FAILED", error_code=code)
        return self.detail(intake_id)

    def detail(self, intake_id: str) -> dict[str, Any]:
        item = self.get(intake_id)
        case_id = item.get("case_id")
        if not case_id or not self.backend.repository.get_case(case_id):
            return item
        case = self.backend.get_case(case_id, role="MAINTAINER")
        return {**item, "candidate": {
            "case_id": case_id, "title": case["title"],
            "facts": {key: case.get("facts", {}).get(key, {}).get("candidate_value") for key in ("symptom", "root_cause", "actions")},
            "evidence": self.backend.repository.list_evidence(case_id),
            "mappings": self.backend.repository.list_mappings(case_id=case_id),
        }}
