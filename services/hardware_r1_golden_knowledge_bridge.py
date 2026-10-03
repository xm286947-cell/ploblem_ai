"""R1 Golden Candidate -> existing Unified Knowledge public-contract bridge."""
from __future__ import annotations

import hashlib
from datetime import datetime
from copy import deepcopy
from typing import Any, Mapping, Protocol, Sequence

from services.hardware_case_knowledge_adapter import (
    HardwareCaseKnowledgeAdapter,
    HardwareKnowledgeAdapterError,
)

GOLDEN_KNOWLEDGE_CONTRACT = "hardware-case-knowledge-object/v1"
FIXED_KNOWLEDGE_REVISION = 1


class HardwareR1GoldenBridgeError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class HardwareR1SourceBinding(Protocol):
    def get_active_source(self, business_case_id: str) -> dict[str, Any]:
        ...

    def add_formal_knowledge_reference(
        self,
        business_case_id: str,
        knowledge_id: str,
        *,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        ...


class HardwareR1GoldenKnowledgeBridge:
    def __init__(
        self,
        adapter: HardwareCaseKnowledgeAdapter,
        source_store: HardwareR1SourceBinding,
    ) -> None:
        self.adapter = adapter
        self.source_store = source_store

    def precheck(
        self,
        golden: Mapping[str, Any],
        evidence_validation: Mapping[str, Any],
    ) -> dict[str, Any]:
        if golden.get("contract_version") != GOLDEN_KNOWLEDGE_CONTRACT:
            raise HardwareR1GoldenBridgeError("GOLDEN_CONTRACT_INVALID")
        identity = golden.get("identity")
        source_fact = golden.get("source_fact")
        review = golden.get("review")
        evidence = golden.get("evidence")
        if not isinstance(identity, Mapping) or not isinstance(source_fact, Mapping):
            raise HardwareR1GoldenBridgeError("GOLDEN_IDENTITY_INVALID")
        if not isinstance(review, Mapping) or review.get("object_status") != "CANDIDATE":
            raise HardwareR1GoldenBridgeError("GOLDEN_STATUS_INVALID")
        if not isinstance(evidence, list) or not evidence:
            raise HardwareR1GoldenBridgeError("GOLDEN_EVIDENCE_MISSING")
        if evidence_validation.get("status") != "PASS":
            raise HardwareR1GoldenBridgeError("EVIDENCE_GATE_NOT_PASSED")
        if int(evidence_validation.get("fabricated_fact_count") or 0):
            raise HardwareR1GoldenBridgeError("FABRICATED_FACT_PRESENT")
        if int(evidence_validation.get("fabricated_block_id_count") or 0):
            raise HardwareR1GoldenBridgeError("FABRICATED_BLOCK_ID_PRESENT")

        case_id = str(identity.get("business_case_id") or "").strip()
        source_id = str(source_fact.get("source_id") or "").strip()
        source = self.source_store.get_active_source(case_id)
        if (
            not case_id
            or len(source_id) != 64
            or source.get("binding_status") != "ACTIVE"
            or source.get("source_status") != "AVAILABLE"
            or str(source.get("source_id") or "") != source_id
            or str(source.get("sha256") or "") != source_id
        ):
            raise HardwareR1GoldenBridgeError("SOURCE_BINDING_MISMATCH")

        seen: set[str] = set()
        for item in evidence:
            if not isinstance(item, Mapping):
                raise HardwareR1GoldenBridgeError("GOLDEN_EVIDENCE_INVALID")
            block_id = str(item.get("block_id") or "").strip()
            if not block_id or block_id in seen:
                raise HardwareR1GoldenBridgeError("GOLDEN_EVIDENCE_INVALID")
            seen.add(block_id)
            if str(item.get("block_type") or "").upper() == "IMAGE":
                raise HardwareR1GoldenBridgeError("IMAGE_EVIDENCE_DEFERRED")
            if not isinstance(item.get("source_locator"), Mapping):
                raise HardwareR1GoldenBridgeError("GOLDEN_EVIDENCE_LOCATOR_INVALID")
            if item.get("text") in (None, ""):
                raise HardwareR1GoldenBridgeError("GOLDEN_EVIDENCE_TEXT_REQUIRED")

        return {
            "status": "PASS",
            "business_case_id": case_id,
            "source_id": source_id,
            "source_ref": source["source_ref"],
            "evidence_count": len(evidence),
            "revision": FIXED_KNOWLEDGE_REVISION,
        }

    def intake(
        self,
        golden: Mapping[str, Any],
        evidence_validation: Mapping[str, Any],
    ) -> dict[str, Any]:
        check = self.precheck(golden, evidence_validation)
        case_id = check["business_case_id"]
        source = self.source_store.get_active_source(case_id)
        source_id = str(source["source_id"])
        source_ref = str(source["source_ref"])
        evidence_results = []
        evidence_refs = []
        for item in golden.get("evidence") or []:
            block_id = str(item["block_id"])
            evidence_id = self.evidence_id(case_id, source_id, block_id)
            locator = dict(item.get("source_locator") or {})
            locator.setdefault("block_id", block_id)
            try:
                result = self.adapter.intake_evidence(
                    case_id=case_id,
                    source_metadata=source,
                    evidence={
                        "evidence_id": evidence_id,
                        "source_ref": source_ref,
                        "evidence_type": str(item.get("block_type") or "TEXT").upper(),
                        "locator": locator,
                        "excerpt_or_caption": str(item.get("text") or ""),
                    },
                    revision=FIXED_KNOWLEDGE_REVISION,
                    source_revision=source_id,
                )
            except HardwareKnowledgeAdapterError as error:
                raise HardwareR1GoldenBridgeError(error.code) from error
            evidence_results.append(result)
            evidence_refs.append(evidence_id)

        structured_content = deepcopy(dict(golden))
        try:
            candidate = self.adapter.intake_candidate(
                case_id=case_id,
                source_document_id=source_id,
                source_ref=source_ref,
                structured_content=structured_content,
                evidence_refs=evidence_refs,
                revision=FIXED_KNOWLEDGE_REVISION,
                source_version=source_id,
            )
        except HardwareKnowledgeAdapterError as error:
            raise HardwareR1GoldenBridgeError(error.code) from error
        if candidate.get("structured_content") != structured_content:
            raise HardwareR1GoldenBridgeError("STRUCTURED_CONTENT_LOSS")
        return {
            "status": "CANDIDATE_INTAKE_PASS",
            "precheck": check,
            "source": source,
            "evidences": evidence_results,
            "candidate": candidate,
        }

    def review(
        self,
        *,
        candidate_id: str,
        original_golden: Mapping[str, Any],
        confirmed_content: Mapping[str, Any],
        reviewer: str,
        review_time: datetime,
        review_comment: str | None = None,
    ) -> dict[str, Any]:
        if confirmed_content.get("contract_version") != GOLDEN_KNOWLEDGE_CONTRACT:
            raise HardwareR1GoldenBridgeError("CONFIRMED_CONTENT_CONTRACT_INVALID")
        if confirmed_content.get("source_fact") != original_golden.get("source_fact"):
            raise HardwareR1GoldenBridgeError("SOURCE_FACT_IMMUTABLE")
        try:
            return self.adapter.review_candidate(
                candidate_id=candidate_id,
                state="CONFIRMED",
                reviewer=reviewer,
                review_time=review_time,
                review_comment=review_comment,
                confirmed_content=deepcopy(dict(confirmed_content)),
                revision=FIXED_KNOWLEDGE_REVISION,
            )
        except HardwareKnowledgeAdapterError as error:
            raise HardwareR1GoldenBridgeError(error.code) from error

    @staticmethod
    def evidence_id(business_case_id: str, source_id: str, block_id: str) -> str:
        digest = hashlib.sha256(
            f"{business_case_id}|{source_id}|{block_id}".encode("utf-8")
        ).hexdigest()
        return "HCR1-EV-" + digest[:24]
