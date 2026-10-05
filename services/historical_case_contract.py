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
REPEAT_RISK_CONTEXT_CONTRACT_VERSION = "historical-case/repeat-risk-context/v1"
SEMANTIC_PROJECTION_CONTRACT_VERSION = "major-semantic-publish/v1"

REPEAT_CAUSE_PATHS = {
    "TRC_OCCURRENCE": ("trc", "occurrence"),
    "TRC_ESCAPE": ("trc", "escape"),
    "MRC_OCCURRENCE": ("mrc", "occurrence"),
    "MRC_ESCAPE": ("mrc", "escape"),
}
REPEAT_ACTION_PATHS = {
    "TECHNICAL_ACTION": "technical_actions",
    "MANAGEMENT_ACTION": "management_actions",
    "CORRECTIVE_ACTION": "corrective_actions",
    "PREVENTIVE_ACTION": "preventive_actions",
}
REPEAT_SEMANTIC_TYPES = (*REPEAT_CAUSE_PATHS, *REPEAT_ACTION_PATHS)


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

    def get_repeat_risk_context(self, case_id: str) -> dict[str, Any]:
        """Return the additive typed projection consumed only by Repeat Risk.

        This is intentionally a separate contract from ``historical-case/v1``.
        Artifact access and exact Evidence resolution remain inside this
        consumer boundary; Repeat Risk never sees repository paths or files.
        """
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
        try:
            self._validate_identity(stable_case_id, artifacts)
        except HistoricalCaseContractError:
            raise
        except Exception as exc:
            raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID") from exc
        case = artifacts.enriched_case or artifacts.standard_case or {}
        if not isinstance(case, dict):
            raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
        metadata = case.get("metadata", {})
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
        declared_contract = metadata.get("semantic_projection_contract")

        if declared_contract is None:
            return {
                "contract_version": REPEAT_RISK_CONTEXT_CONTRACT_VERSION,
                "case_id": stable_case_id,
                "semantic_mode": "LEGACY_GENERIC_ONLY",
                "semantic_contract_version": None,
                "typed_causes": [],
                "typed_actions": [],
                "semantic_coverage": {
                    entry_type: "LEGACY_GENERIC_ONLY" for entry_type in REPEAT_SEMANTIC_TYPES
                },
                "semantic_evidence_status": "LEGACY_GENERIC_ONLY",
            }
        if declared_contract != SEMANTIC_PROJECTION_CONTRACT_VERSION:
            raise HistoricalCaseContractError("CASE_SEMANTIC_CONTRACT_UNSUPPORTED")

        analysis = case.get("analysis")
        solution = case.get("solution")
        if not isinstance(analysis, dict) or not isinstance(solution, dict):
            raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")

        raw_artifact = artifacts.raw_evidence
        if raw_artifact is not None and not isinstance(raw_artifact, dict):
            raise HistoricalCaseContractError("CASE_SEMANTIC_EVIDENCE_INVALID")
        raw_sections = (raw_artifact or {}).get("sections")
        if raw_sections is None:
            raw_sections = []
        if not isinstance(raw_sections, list):
            raise HistoricalCaseContractError("CASE_SEMANTIC_EVIDENCE_INVALID")
        evidence_index: dict[str, list[dict[str, Any]]] = {}
        for section in raw_sections:
            if not isinstance(section, dict):
                continue
            evidence_id = _text(section.get("evidence_id"))
            if evidence_id:
                evidence_index.setdefault(evidence_id, []).append(section)

        typed_causes: list[dict[str, Any]] = []
        typed_actions: list[dict[str, Any]] = []
        coverage: dict[str, str] = {}
        for entry_type, (family, side) in REPEAT_CAUSE_PATHS.items():
            group = analysis.get(family)
            if group is not None and not isinstance(group, dict):
                raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
            slot = group.get(side) if isinstance(group, dict) else None
            if slot is None:
                coverage[entry_type] = "MISSING"
                continue
            if not isinstance(slot, dict):
                raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
            value = slot.get("standard")
            refs = slot.get("evidence_refs")
            if not isinstance(value, str) or not isinstance(refs, list):
                raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
            if not value.strip():
                if refs:
                    raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
                coverage[entry_type] = "MISSING"
                continue
            item = self._typed_semantic_item(
                entry_type,
                value,
                slot.get("source_type"),
                refs,
                evidence_index,
            )
            typed_causes.append(item)
            coverage[entry_type] = "PRESENT"

        for entry_type, field in REPEAT_ACTION_PATHS.items():
            values = solution.get(field)
            if values is None:
                coverage[entry_type] = "MISSING"
                continue
            if not isinstance(values, list):
                raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
            if not values:
                coverage[entry_type] = "MISSING"
                continue
            for value_record in values:
                if not isinstance(value_record, dict):
                    raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
                value = value_record.get("value")
                refs = value_record.get("evidence_refs")
                if not isinstance(value, str) or not value.strip() or not isinstance(refs, list):
                    raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
                typed_actions.append(
                    self._typed_semantic_item(
                        entry_type,
                        value,
                        value_record.get("source_type"),
                        refs,
                        evidence_index,
                    )
                )
            coverage[entry_type] = "PRESENT"

        return {
            "contract_version": REPEAT_RISK_CONTEXT_CONTRACT_VERSION,
            "case_id": stable_case_id,
            "semantic_mode": "TYPED",
            "semantic_contract_version": SEMANTIC_PROJECTION_CONTRACT_VERSION,
            "typed_causes": typed_causes,
            "typed_actions": typed_actions,
            "semantic_coverage": coverage,
            "semantic_evidence_status": "COMPLETE" if typed_causes or typed_actions else "NO_TYPED_SEMANTICS",
        }

    @staticmethod
    def _typed_semantic_item(
        entry_type: str,
        value: str,
        declared_source_type: Any,
        refs: list[Any],
        evidence_index: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        if not refs:
            raise HistoricalCaseContractError("CASE_SEMANTIC_EVIDENCE_INVALID")
        evidence: list[dict[str, Any]] = []
        modalities: set[str] = set()
        all_modalities_defensible = True
        for reference in refs:
            if not isinstance(reference, dict):
                raise HistoricalCaseContractError("CASE_SEMANTIC_EVIDENCE_INVALID")
            source_location = _text(reference.get("source_location"))
            quote = reference.get("quote")
            if (
                not source_location
                or not source_location.startswith("evidence://")
                or not isinstance(quote, str)
            ):
                raise HistoricalCaseContractError("CASE_SEMANTIC_EVIDENCE_INVALID")
            evidence_id = source_location.removeprefix("evidence://")
            if not evidence_id or "/" in evidence_id:
                raise HistoricalCaseContractError("CASE_SEMANTIC_EVIDENCE_INVALID")
            matches = evidence_index.get(evidence_id, [])
            if len(matches) != 1:
                raise HistoricalCaseContractError("CASE_SEMANTIC_EVIDENCE_INVALID")
            section = matches[0]
            raw_text = section.get("raw_text")
            if (
                section.get("entry_type") != entry_type
                or not isinstance(raw_text, str)
                or not raw_text.strip()
                or quote != raw_text
            ):
                raise HistoricalCaseContractError("CASE_SEMANTIC_EVIDENCE_INVALID")
            reference_source_type = _text(reference.get("source_type"))
            section_source_types = {
                _text(section.get("source_modality")),
                _text(section.get("source_type")),
            } - {None}
            if (
                reference_source_type
                and section_source_types
                and reference_source_type not in section_source_types
            ):
                raise HistoricalCaseContractError("CASE_SEMANTIC_EVIDENCE_INVALID")

            modality = _text(section.get("source_modality"))
            if modality in {"EXCEL", "PDF"}:
                modalities.add(modality)
            else:
                all_modalities_defensible = False
            public_source_type = modality or _text(section.get("source_type"))
            evidence.append({
                "evidence_id": evidence_id,
                "source_type": public_source_type,
                "source_id": section.get("source_id"),
                "source_version": section.get("source_version"),
                "source_ref": section.get("source_ref"),
                "origin_source_id": section.get("origin_source_id"),
                "origin_source_version": section.get("origin_source_version"),
                "origin_source_ref": section.get("origin_source_ref"),
                "file_name": section.get("file_name"),
                "page": section.get("page"),
                "section": section.get("section"),
                "raw_text": raw_text,
                "url": section.get("url"),
            })

        projected_source_type = None
        if all_modalities_defensible:
            projected_source_type = (
                "FUSED" if modalities == {"EXCEL", "PDF"}
                else next(iter(modalities)) if len(modalities) == 1
                else None
            )
        declared = _text(declared_source_type)
        if declared in {"EXCEL", "PDF", "FUSED"} and declared != projected_source_type:
            raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
        return {
            "semantic_type": entry_type,
            "value": value,
            "source_type": projected_source_type,
            "evidence": evidence,
        }

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
        solution_groups = [
            ("纠正措施", _values(solution.get("corrective_actions"))),
            ("预防措施", _values(solution.get("preventive_actions"))),
            ("技术措施", _values(solution.get("technical_actions"))),
            ("管理措施", _values(solution.get("management_actions"))),
            ("可复用措施", _values(solution.get("reusable_actions"))),
        ]
        populated_solution_groups = [
            (label, values) for label, values in solution_groups if values
        ]
        if len(populated_solution_groups) == 1:
            solution_text = "\n".join(populated_solution_groups[0][1])
        elif populated_solution_groups:
            solution_text = "\n".join(
                f"{label}: {'; '.join(values)}"
                for label, values in populated_solution_groups
            )
        else:
            solution_text = None
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
        default_source_id = _first_text(
            raw_evidence.get("source_id"),
            raw_evidence.get("itr_id"),
            metadata.get("itr_id"),
            case_id,
        )
        default_file_name = _first_text(
            raw_evidence.get("report_filename"),
            metadata.get("report_filename"),
        )
        default_source_type = _first_text(raw_evidence.get("source_type"), "REPORT")
        evidence: list[dict[str, Any]] = []
        for section in sections:
            if not isinstance(section, dict):
                continue
            pages = section.get("page_numbers")
            page = pages[0] if isinstance(pages, list) and pages else section.get("page")
            evidence.append({
                "evidence_id": _text(section.get("evidence_id")),
                "source_type": _first_text(section.get("source_type"), default_source_type),
                "source_id": _first_text(section.get("source_id"), default_source_id),
                "source_version": _first_text(
                    section.get("source_version"),
                    section.get("revision_id"),
                ),
                "source_ref": _text(section.get("source_ref")),
                "file_name": _first_text(section.get("file_name"), default_file_name),
                "page": page if isinstance(page, int) else None,
                "section": _first_text(section.get("section"), section.get("section_type")),
                "raw_text": _first_text(section.get("raw_text"), section.get("content"), section.get("text")),
                "url": (
                    _text(section.get("url"))
                    or _text(section.get("source_reference"))
                    or _text(raw_evidence.get("url"))
                ),
            })
        return evidence
