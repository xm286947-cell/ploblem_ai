"""Public Historical Case consumer contract.

This module deliberately projects existing Repeat Risk artifacts.  It never
exposes their file layout, database rows, or retrieval implementation details.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import yaml

from repositories import JsonArtifactRepository, RepositoryError
from retriever.case_retriever import CaseRetriever, QueryInput
from services.knowledge_service import KnowledgeArtifacts, KnowledgeService


CONTRACT_VERSION = "historical-case/v1"


class HistoricalCaseContractError(RuntimeError):
    """A stable consumer-facing error; callers must use ``code`` only."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, dict):
            value = value.get("value")
        result = _text(value)
        if result:
            return result
    return None


def _values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _first_text(item)
        if text and text not in result:
            result.append(text)
    return result


class HistoricalCaseConsumerService:
    """Stable Search/Detail facade for downstream consumers.

    ``repeat_search`` is an adapter over the existing Repeat Risk search, not a
    replacement for it.  It receives ``(QueryInput, top_k)`` and returns the
    existing result dictionary containing ``results``.
    """

    def __init__(
        self,
        repository: JsonArtifactRepository,
        *,
        repeat_search: Callable[[QueryInput, int | None], dict[str, Any]] | None = None,
    ) -> None:
        self.repository = repository
        self.knowledge = KnowledgeService(repository)
        self.repeat_search = repeat_search

    @classmethod
    def from_project_root(cls, root: str | Path) -> "HistoricalCaseConsumerService":
        project_root = Path(root).resolve()
        try:
            app = yaml.safe_load((project_root / "config/app.yaml").read_text(encoding="utf-8"))
            model = yaml.safe_load((project_root / "config/model.yaml").read_text(encoding="utf-8"))
            retrieval = yaml.safe_load((project_root / "config/retrieval.yaml").read_text(encoding="utf-8"))
            retriever = CaseRetriever(project_root, app, model, retrieval)
        except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
            raise HistoricalCaseContractError("CASE_SERVICE_UNAVAILABLE") from exc

        return cls(
            JsonArtifactRepository(project_root),
            repeat_search=lambda query, top_k: retriever.search(query, top_k=top_k),
        )

    def search_repeat_cases(
        self,
        query: QueryInput,
        *,
        top_k: int | None = None,
    ) -> dict[str, Any]:
        """Return consumer candidates without leaking existing search internals."""
        if self.repeat_search is None:
            raise HistoricalCaseContractError("CASE_SERVICE_UNAVAILABLE")
        try:
            result = self.repeat_search(query, top_k)
        except HistoricalCaseContractError:
            raise
        except Exception as exc:
            raise HistoricalCaseContractError("CASE_SERVICE_UNAVAILABLE") from exc

        raw_candidates = result.get("results") if isinstance(result, dict) else None
        if not isinstance(raw_candidates, list):
            raise HistoricalCaseContractError("CASE_CONTRACT_INVALID")
        candidates = [self._candidate(item) for item in raw_candidates]
        return {
            "contract_version": CONTRACT_VERSION,
            "candidates": candidates,
        }

    @staticmethod
    def _candidate(item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise HistoricalCaseContractError("CASE_CONTRACT_INVALID")
        case_id = _text(item.get("case_id"))
        if not case_id:
            raise HistoricalCaseContractError("CASE_CONTRACT_INVALID")
        score = item.get("score")
        rank = item.get("rank")
        if not isinstance(score, (int, float)) or not isinstance(rank, int):
            raise HistoricalCaseContractError("CASE_CONTRACT_INVALID")
        candidate: dict[str, Any] = {
            "case_id": case_id,
            "title": _text(item.get("title")),
            "summary": _text(item.get("summary")) or _text(item.get("title")),
            "score": float(score),
            "rank": rank,
        }
        reasons = item.get("reasons")
        if isinstance(reasons, list):
            candidate["retrieval_reason"] = [str(reason) for reason in reasons if _text(reason)]
        matched_fields = item.get("matched_fields")
        if isinstance(matched_fields, list):
            candidate["matched_fields"] = [str(field) for field in matched_fields if _text(field)]
        return candidate

    def list_published_cases(
        self,
        *,
        q: str = "",
        product: str = "",
        status: str = "PUBLISHED",
    ) -> dict[str, Any]:
        """List published Historical Cases without exposing repository layout."""
        wanted_status = (_text(status) or "PUBLISHED").upper()
        query = (_text(q) or "").lower()
        wanted_product = (_text(product) or "").lower()
        items: list[dict[str, Any]] = []

        try:
            paths = self.repository.list("knowledge/publication_metadata/major_event")
        except Exception as exc:
            raise HistoricalCaseContractError("CASE_SERVICE_UNAVAILABLE") from exc

        for path in paths:
            metadata = self.repository.load(path)
            if not isinstance(metadata, dict):
                continue
            publication_status = (_text(metadata.get("publication_status")) or "").upper()
            if wanted_status and publication_status != wanted_status:
                continue
            case_id = _text(metadata.get("case_id"))
            if not case_id:
                continue
            try:
                detail = self.get_case(case_id)
            except HistoricalCaseContractError:
                continue
            item_product = _text(detail.get("product"))
            itr = _text(metadata.get("business_id"))
            title = _text(detail.get("title"))
            haystack = " ".join(x for x in (case_id, itr, title, item_product) if x).lower()
            if query and query not in haystack:
                continue
            if wanted_product and (item_product or "").lower() != wanted_product:
                continue
            items.append(
                {
                    "case_id": case_id,
                    "itr": itr,
                    "title": title,
                    "product": item_product,
                    "version": _text(detail.get("version")),
                    "status": publication_status,
                    "published_at": _text(metadata.get("published_at")),
                    "case_version": _text(metadata.get("knowledge_revision")),
                }
            )

        items.sort(
            key=lambda item: (
                item.get("published_at") or "",
                item.get("case_id") or "",
            ),
            reverse=True,
        )
        return {
            "contract_version": CONTRACT_VERSION,
            "items": items,
            "total": len(items),
            "status_filter": wanted_status,
        }

    def get_case(self, case_id: str) -> dict[str, Any]:
        """Load one historical case by stable business ID only."""
        stable_case_id = _text(case_id)
        if not stable_case_id:
            raise HistoricalCaseContractError("CASE_NOT_FOUND")
        try:
            artifacts = self.knowledge.load_case_artifacts(stable_case_id)
        except RepositoryError as exc:
            code = "CASE_ACCESS_DENIED" if "PATH_OUTSIDE" in str(exc) else "CASE_SERVICE_UNAVAILABLE"
            raise HistoricalCaseContractError(code) from exc
        except Exception as exc:
            raise HistoricalCaseContractError("CASE_SERVICE_UNAVAILABLE") from exc

        if not any((artifacts.retrieval_document, artifacts.enriched_case, artifacts.standard_case)):
            raise HistoricalCaseContractError("CASE_NOT_FOUND")
        self._validate_identity(stable_case_id, artifacts)
        return self._detail(stable_case_id, artifacts)

    @staticmethod
    def _validate_identity(case_id: str, artifacts: KnowledgeArtifacts) -> None:
        for artifact in (
            artifacts.retrieval_document,
            artifacts.enriched_case,
            artifacts.standard_case,
            artifacts.raw_evidence,
        ):
            if not isinstance(artifact, dict):
                continue
            declared = _text(artifact.get("case_id")) or _text(
                (artifact.get("metadata") or {}).get("case_id")
            )
            if declared and declared != case_id:
                raise HistoricalCaseContractError("CASE_CONTRACT_INVALID")

    def _detail(self, case_id: str, artifacts: KnowledgeArtifacts) -> dict[str, Any]:
        document = artifacts.retrieval_document or {}
        case = artifacts.enriched_case or artifacts.standard_case or {}
        metadata = case.get("metadata") or {}
        business = case.get("business_context") or {}
        problem = case.get("problem") or {}
        analysis = case.get("analysis") or {}
        solution = case.get("solution") or {}

        root_cause = _first_text(
            *(_values(analysis.get("root_cause"))),
            _first_text(((analysis.get("trc") or {}).get("occurrence") or {}).get("standard")),
            _first_text(((analysis.get("mrc") or {}).get("occurrence") or {}).get("standard")),
        )
        solution_text = _first_text(
            *(_values(solution.get("corrective_actions"))),
            *(_values(solution.get("preventive_actions"))),
            *(_values(solution.get("reusable_actions"))),
        )
        symptom_values = _values(problem.get("phenomenon"))
        symptom = "\n".join(symptom_values) if symptom_values else None

        return {
            "contract_version": CONTRACT_VERSION,
            "case_id": case_id,
            "title": _first_text(document.get("title"), (case.get("knowledge") or {}).get("case_summary")),
            "problem_description": _first_text(
                problem.get("standard_description"),
                problem.get("report_description"),
                problem.get("original_description"),
                document.get("text"),
            ),
            "product": _first_text(business.get("product"), case.get("product")),
            "device_type": _first_text(business.get("device_type"), case.get("device_type")),
            "device_model": _first_text(business.get("device_model"), case.get("device_model")),
            "symptom": symptom,
            "root_cause": root_cause,
            "solution": solution_text,
            "verification_result": _first_text(
                solution.get("verification_result"),
                solution.get("action_status"),
            ),
            "status": _first_text(case.get("status"), metadata.get("parse_status")),
            "evidence": self._evidence(case_id, artifacts.raw_evidence, metadata),
        }

    @staticmethod
    def _evidence(
        case_id: str,
        raw_evidence: dict[str, Any] | None,
        metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_evidence, dict):
            return []
        sections = raw_evidence.get("sections")
        if not isinstance(sections, list):
            return []
        source_id = _first_text(raw_evidence.get("source_id"), raw_evidence.get("itr_id"), metadata.get("itr_id"), case_id)
        file_name = _first_text(raw_evidence.get("report_filename"), metadata.get("report_filename"))
        source_type = _first_text(raw_evidence.get("source_type"), "REPORT")
        evidence: list[dict[str, Any]] = []
        for section in sections:
            if not isinstance(section, dict):
                continue
            pages = section.get("page_numbers")
            page = pages[0] if isinstance(pages, list) and pages else section.get("page")
            evidence.append({
                "source_type": source_type,
                "source_id": source_id,
                "file_name": file_name,
                "page": page if isinstance(page, int) else None,
                "section": _first_text(section.get("section"), section.get("section_type")),
                "raw_text": _first_text(section.get("raw_text"), section.get("content"), section.get("text")),
                "url": _text(section.get("url")) or _text(raw_evidence.get("url")),
            })
        return evidence
