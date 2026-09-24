"""Hardware Case M4 AI Adapter.

The adapter does not implement a Provider or Runtime.  A structuring callable
is injected from the existing Unified Runtime boundary.  The adapter converts
a real DOCX into contract-shaped AI Candidate fields, grounded Evidence, and
SUGGESTED mappings; it never auto-confirms or auto-publishes knowledge.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
from typing import Any, Callable, Protocol

from services.hardware_case_backend import HardwareCaseBackendService
from services.hardware_case_source_store import HardwareCaseSourceStore
from services.hardware_case_word import HardwareWordParseError, ParsedWord, parse_docx


CASE_NAME = re.compile(r"^\s*(A\d{4,})\s*[-—–_]\s*(.+?)\s*$", re.IGNORECASE)

FACT_FIELDS = (
    "background",
    "symptom",
    "impact",
    "occurrence_condition",
    "analysis_process",
    "failure_mode",
    "root_cause",
    "failure_mechanism",
    "actions",
    "verification_result",
    "conclusion",
)


class RuntimeStructurer(Protocol):
    def __call__(self, document: dict[str, Any]) -> dict[str, Any]:
        """Return structured candidate JSON produced through Unified Runtime."""


class HardwareCaseAIAdapterError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def derive_identity(parsed: ParsedWord) -> tuple[str, str]:
    stem = Path(parsed.file_name).stem
    match = CASE_NAME.match(stem)
    if match:
        return match.group(1).upper(), match.group(2).strip()
    generated = "HC-SRC-" + parsed.source_id[:12].upper()
    title = stem.strip() or generated
    return generated, title


def _candidate_field(value: Any, evidence_refs: list[str]) -> dict[str, Any]:
    return {
        "candidate_value": value,
        "confirmed_value": None,
        "review_disposition": "UNREVIEWED",
        "evidence_refs": evidence_refs,
    }


class HardwareCaseAIAdapter:
    def __init__(
        self,
        backend: HardwareCaseBackendService,
        structurer: RuntimeStructurer | Callable[[dict[str, Any]], dict[str, Any]],
        *,
        source_store: HardwareCaseSourceStore | None = None,
    ):
        self.backend = backend
        self.structurer = structurer
        self.source_store = source_store

    def ingest_docx(self, path: str | Path) -> dict[str, Any]:
        try:
            parsed = parse_docx(path)
        except HardwareWordParseError as exc:
            raise HardwareCaseAIAdapterError(exc.code) from exc

        case_id, title = derive_identity(parsed)
        source_ref = parsed.source_ref
        if self.source_store is not None:
            try:
                self.source_store.register_file(source_ref, path)
            except Exception as exc:
                code = getattr(exc, "code", "SOURCE_REGISTRATION_FAILED")
                raise HardwareCaseAIAdapterError(str(code)) from exc
        block_index = {block["block_id"]: block for block in parsed.blocks}

        try:
            result = self.structurer(parsed.to_dict())
        except Exception:
            failed = {
                "case_id": case_id,
                "title": title,
                "case_status": "PENDING_REVIEW",
                "processing_status": "STRUCTURE_EXTRACTION_FAILED",
                "source_refs": [source_ref],
                "product_context": {},
                "facts": {},
            }
            self.backend.create_case(failed)
            return {
                "case_id": case_id,
                "status": "FAILED",
                "processing_status": "STRUCTURE_EXTRACTION_FAILED",
                "warnings": ["STRUCTURE_EXTRACTION_FAILED"],
            }

        if not isinstance(result, dict):
            raise HardwareCaseAIAdapterError("AI_CONTRACT_INVALID")

        warnings: list[str] = []
        evidence_by_block: dict[str, str] = {}
        evidence_count = 0

        requested_evidence: set[str] = set()
        facts_payload = result.get("facts")
        if not isinstance(facts_payload, dict):
            facts_payload = {}
            warnings.append("FACTS_MISSING")

        for field_name, payload in facts_payload.items():
            if field_name not in FACT_FIELDS or not isinstance(payload, dict):
                continue
            for block_id in payload.get("evidence_block_ids") or []:
                requested_evidence.add(str(block_id))

        for mapping_key in ("circuit_feature_links", "material_links"):
            values = result.get(mapping_key)
            if not isinstance(values, list):
                continue
            for item in values:
                if not isinstance(item, dict):
                    continue
                for block_id in item.get("evidence_block_ids") or []:
                    requested_evidence.add(str(block_id))

        for block_id in sorted(requested_evidence):
            block = block_index.get(block_id)
            if block is None:
                warnings.append(f"EVIDENCE_BLOCK_NOT_FOUND:{block_id}")
                continue
            evidence_id = f"EV-{case_id}-{block_id}"
            evidence = {
                "evidence_id": evidence_id,
                "case_id": case_id,
                "source_ref": source_ref,
                "evidence_type": (
                    "IMAGE"
                    if block["block_type"] == "IMAGE"
                    else "TABLE"
                    if block["block_type"] == "TABLE"
                    else "TEXT"
                ),
                "locator": dict(block["source_locator"]),
                "excerpt_or_caption": (
                    block.get("text")
                    if block["block_type"] != "IMAGE"
                    else block.get("image_ref")
                ),
                "evidence_status": "AVAILABLE",
            }
            evidence_by_block[block_id] = evidence_id
            evidence_count += 1

        facts: dict[str, Any] = {}
        for field_name in FACT_FIELDS:
            payload = facts_payload.get(field_name)
            if not isinstance(payload, dict):
                continue
            value = payload.get("value")
            block_ids = [str(item) for item in payload.get("evidence_block_ids") or []]
            refs = [
                evidence_by_block[block_id]
                for block_id in block_ids
                if block_id in evidence_by_block
            ]
            # A locator alone is insufficient: an unrelated block cannot ground
            # a claimed fact. Mock/real semantic outputs remain review candidates.
            if value not in (None, "") and field_name in {"symptom", "root_cause", "actions"}:
                supported = any(
                    str(value).strip() in str(block_index[block_id].get("text") or "")
                    for block_id in block_ids
                    if block_id in block_index
                )
                if not supported:
                    refs = []
                    warnings.append(f"KEY_FACT_UNSUPPORTED:{field_name}")
            if value not in (None, "") and not refs and field_name in {
                "symptom",
                "root_cause",
                "actions",
            }:
                warnings.append(f"KEY_FACT_EVIDENCE_MISSING:{field_name}")
                value = None
            facts[field_name] = _candidate_field(value, refs)

        case = {
            "case_id": case_id,
            "title": str(result.get("title") or title).strip() or title,
            "case_status": "PENDING_REVIEW",
            "processing_status": "READY",
            "source_refs": [source_ref],
            "product_context": (
                result.get("product_context")
                if isinstance(result.get("product_context"), dict)
                else {}
            ),
            "facts": facts,
        }
        self.backend.create_case(case)

        for block_id, evidence_id in evidence_by_block.items():
            block = block_index[block_id]
            self.backend.save_evidence(
                {
                    "evidence_id": evidence_id,
                    "case_id": case_id,
                    "source_ref": source_ref,
                    "evidence_type": (
                        "IMAGE"
                        if block["block_type"] == "IMAGE"
                        else "TABLE"
                        if block["block_type"] == "TABLE"
                        else "TEXT"
                    ),
                    "locator": dict(block["source_locator"]),
                    "excerpt_or_caption": (
                        block.get("text")
                        if block["block_type"] != "IMAGE"
                        else block.get("image_ref")
                    ),
                    "evidence_status": "AVAILABLE",
                }
            )

        circuit_count = self._persist_suggestions(
            case_id,
            "CIRCUIT_FEATURE",
            result.get("circuit_feature_links"),
            evidence_by_block,
            warnings,
        )
        material_count = self._persist_suggestions(
            case_id,
            "MATERIAL_DEVICE",
            result.get("material_links"),
            evidence_by_block,
            warnings,
        )

        status = "SUCCESS"
        if warnings:
            status = "NEEDS_REVIEW"
        elif not facts:
            status = "PARTIAL"

        return {
            "case_id": case_id,
            "status": status,
            "processing_status": "READY",
            "evidence_count": evidence_count,
            "circuit_suggestion_count": circuit_count,
            "material_suggestion_count": material_count,
            "warnings": warnings,
            "source_id": parsed.source_id,
        }

    def _persist_suggestions(
        self,
        case_id: str,
        tree_type: str,
        values: Any,
        evidence_by_block: dict[str, str],
        warnings: list[str],
    ) -> int:
        if not isinstance(values, list):
            return 0
        count = 0
        for index, item in enumerate(values, start=1):
            if not isinstance(item, dict):
                continue
            node_id = str(item.get("node_id") or "").strip()
            if not node_id:
                warnings.append(f"MAPPING_NODE_MISSING:{tree_type}:{index}")
                continue
            node = self.backend.repository.get_tree_node(node_id)
            if node is None or node.get("tree_type") != tree_type:
                warnings.append(f"MAPPING_NODE_NOT_FOUND:{tree_type}:{node_id}")
                continue
            refs = [
                evidence_by_block[str(block_id)]
                for block_id in item.get("evidence_block_ids") or []
                if str(block_id) in evidence_by_block
            ]
            digest = sha256(
                f"{case_id}|{tree_type}|{node_id}|{index}".encode("utf-8")
            ).hexdigest()[:16]
            self.backend.set_mapping(
                {
                    "mapping_id": f"MAP-AI-{digest}",
                    "case_id": case_id,
                    "tree_type": tree_type,
                    "node_id": node_id,
                    "relation_role": "PRIMARY" if index == 1 else "SECONDARY",
                    "mapping_status": "SUGGESTED",
                    "confidence": item.get("confidence"),
                    "basis_refs": refs,
                }
            )
            count += 1
        return count
