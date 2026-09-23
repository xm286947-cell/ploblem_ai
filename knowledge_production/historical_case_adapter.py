from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from repositories import JsonArtifactRepository
from runtime.contracts import EvidenceLocator, EvidenceReference, SourceRef

from .intake import BusinessCandidateIntakeService
from .models import BusinessCandidateInput


class HistoricalCaseCandidateError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class HistoricalCaseCandidateProducer:
    """Map historical-case/v1 into generic business evidence + candidate intake."""

    def __init__(self, repository: JsonArtifactRepository) -> None:
        self.repository = repository
        self.intake = BusinessCandidateIntakeService(repository)

    def intake_solution(
        self,
        historical_case: dict[str, Any],
        *,
        created_at: datetime,
    ):
        if not isinstance(historical_case, dict):
            raise HistoricalCaseCandidateError("CASE_CONTRACT_INVALID")
        if historical_case.get("contract_version") != "historical-case/v1":
            raise HistoricalCaseCandidateError("CASE_CONTRACT_INVALID")

        case_id = str(historical_case.get("case_id") or "").strip()
        solution = str(historical_case.get("solution") or "").strip()
        if not case_id:
            raise HistoricalCaseCandidateError("CASE_CONTRACT_INVALID")
        if not solution:
            raise HistoricalCaseCandidateError("CASE_KNOWLEDGE_EMPTY")

        evidence_ids = self._bind_case_evidence(case_id, historical_case)
        if not evidence_ids:
            raise HistoricalCaseCandidateError("EVIDENCE_MISSING")

        payload = BusinessCandidateInput(
            candidate_id=self._candidate_id(case_id, solution),
            candidate_source_type="BUSINESS",
            business_source_type="HISTORICAL_CASE",
            business_source_id=case_id,
            business_source_version=None,
            object_type="SOLUTION",
            title=(
                str(historical_case.get("title") or "").strip()
                or f"Historical Case {case_id} solution"
            ),
            summary=str(historical_case.get("problem_description") or "").strip() or None,
            content=solution,
            device_type=str(historical_case.get("device_type") or "").strip() or None,
            scope=["historical_case_solution"],
            conditions=[
                value
                for value in [
                    str(historical_case.get("symptom") or "").strip(),
                    str(historical_case.get("root_cause") or "").strip(),
                ]
                if value
            ],
            limitations=[],
            tags=["historical_case", "solution"],
            source_refs=[],
            evidence_refs=evidence_ids,
            confidence=None,
            created_at=created_at,
            producer="historical-case/v1",
            contract_version="knowledge-candidate/v1",
            metadata={
                "historical_case_title": historical_case.get("title"),
                "verification_result": historical_case.get("verification_result"),
                "historical_case_status": historical_case.get("status"),
            },
        )
        return self.intake.intake(payload)

    def _bind_case_evidence(
        self,
        case_id: str,
        historical_case: dict[str, Any],
    ) -> list[str]:
        raw_evidence = historical_case.get("evidence")
        if not isinstance(raw_evidence, list):
            return []

        evidence_ids: list[str] = []
        for index, item in enumerate(raw_evidence):
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("source_id") or "").strip()
            if not source_id:
                continue
            raw_text = str(item.get("raw_text") or "").strip()
            section = str(item.get("section") or "").strip() or None
            page = item.get("page")
            if page is not None and not isinstance(page, int):
                page = None
            source_type = str(item.get("source_type") or "REPORT").strip() or "REPORT"
            fingerprint_material = {
                "case_id": case_id,
                "source_id": source_id,
                "source_type": source_type,
                "page": page,
                "section": section,
                "raw_text": raw_text,
                "url": item.get("url"),
                "index": index,
            }
            digest = hashlib.sha256(
                json.dumps(
                    fingerprint_material,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            evidence_id = "EVD-HCASE-" + digest[:24]
            locator_value = {
                "case_id": case_id,
                "source_id": source_id,
                "section": section,
            }
            if page is not None:
                locator_value["page"] = page

            evidence = EvidenceReference(
                evidence_id=evidence_id,
                source=SourceRef(
                    source_id=source_id,
                    source_type=source_type,
                    revision=None,
                    content_hash=None,
                    fingerprint=f"historical-case:{digest}",
                    uri=(
                        str(item.get("url") or "").strip()
                        or None
                    ),
                    metadata={
                        "historical_case_id": case_id,
                        "contract_version": "historical-case/v1",
                        "file_name": item.get("file_name"),
                    },
                ),
                locator=EvidenceLocator(
                    type="BUSINESS_RECORD",
                    value=locator_value,
                ),
                excerpt=raw_text or None,
                metadata={
                    "evidence_status": "BOUND",
                    "historical_case_id": case_id,
                },
            )
            path = f"knowledge/production/evidence/{evidence_id}.json"
            payload = evidence.model_dump(mode="json")
            existing = self.repository.load(path)
            if existing is not None and existing != payload:
                raise HistoricalCaseCandidateError("EVIDENCE_ID_CONFLICT")
            self.repository.save(path, payload)
            evidence_ids.append(evidence_id)
        return list(dict.fromkeys(evidence_ids))

    @staticmethod
    def _candidate_id(case_id: str, solution: str) -> str:
        digest = hashlib.sha256(
            f"{case_id}|SOLUTION|{solution}".encode("utf-8")
        ).hexdigest()
        return "BC-HCASE-" + digest[:24]
