from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol

from retriever.case_retriever import QueryInput
from services.historical_case_contract import (
    CONTRACT_VERSION as HISTORICAL_CASE_CONTRACT_VERSION,
    REPEAT_RISK_CONTEXT_CONTRACT_VERSION,
    SEMANTIC_PROJECTION_CONTRACT_VERSION,
    REPEAT_SEMANTIC_TYPES,
    HistoricalCaseContractError,
)

from .repository import RepeatQueryTraceRepository


SEARCH_SUCCESS = "SUCCESS"
SEARCH_NO_CANDIDATES = "NO_CANDIDATES"
SEARCH_UNAVAILABLE = "SEARCH_UNAVAILABLE"
SEARCH_INCOMPLETE = "INCOMPLETE"


class HistoricalCaseSearchClient(Protocol):
    """Public Historical Case contract consumed by Repeat Risk only."""

    def search_repeat_cases(
        self,
        query: QueryInput,
        *,
        top_k: int | None = None,
    ) -> dict[str, Any]: ...

    def get_case(self, case_id: str) -> dict[str, Any]: ...

    def get_repeat_risk_context(self, case_id: str) -> dict[str, Any]: ...


class RepeatSearchContractError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _flatten_text(value: Any) -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        for child in value.values():
            result.extend(_flatten_text(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            result.extend(_flatten_text(child))
    else:
        text = _text(value)
        if text:
            result.append(text)
    return result


def _pick(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _collect(mapping: dict[str, Any], *keys: str) -> list[str]:
    values: list[str] = []
    for key in keys:
        value = mapping.get(key)
        if value in (None, "", [], {}):
            continue
        for item in _flatten_text(value):
            if item not in values:
                values.append(item)
    return values


class RepeatHistoricalCaseSearchService:
    """REPEAT-SEARCH-001 implementation.

    Repeat Risk consumes Historical Case strictly through the public
    historical-case/v1 contract.  It never opens Major/Knowledge databases,
    JSON artifacts, retrieval indexes, or repository-internal paths.
    """

    def __init__(
        self,
        trace_repository: RepeatQueryTraceRepository,
        case_client: HistoricalCaseSearchClient,
    ) -> None:
        self.trace_repository = trace_repository
        self.case_client = case_client

    def search(self, query_id: str, *, top_k: int = 5) -> dict[str, Any]:
        trace = self.trace_repository.get(query_id)
        if not trace:
            raise RepeatSearchContractError("REPEAT_QUERY_NOT_FOUND")

        query = self._to_query_input(trace)
        base = {
            "query_id": query_id,
            "subject_ref": trace["subject_ref"],
            "query_input": query.to_dict(),
            "historical_case_contract": HISTORICAL_CASE_CONTRACT_VERSION,
            "status": SEARCH_UNAVAILABLE,
            "candidates": [],
            "error_code": None,
        }

        try:
            search_result = self.case_client.search_repeat_cases(query, top_k=top_k)
        except HistoricalCaseContractError as exc:
            base["status"] = self._search_error_status(exc.code)
            base["error_code"] = exc.code
            return base
        except Exception:
            base["status"] = SEARCH_UNAVAILABLE
            base["error_code"] = "CASE_SERVICE_UNAVAILABLE"
            return base

        if search_result.get("contract_version") != HISTORICAL_CASE_CONTRACT_VERSION:
            base["status"] = SEARCH_INCOMPLETE
            base["error_code"] = "CASE_CONTRACT_INVALID"
            return base

        raw_candidates = search_result.get("candidates")
        if not isinstance(raw_candidates, list):
            base["status"] = SEARCH_INCOMPLETE
            base["error_code"] = "CASE_CONTRACT_INVALID"
            return base

        if not raw_candidates:
            base["status"] = SEARCH_NO_CANDIDATES
            return base

        candidates: list[dict[str, Any]] = []
        incomplete = False
        for item in raw_candidates:
            candidate = self._base_candidate(item)
            case_id = candidate["case_id"]
            try:
                semantic_reader = getattr(self.case_client, "get_repeat_risk_context", None)
                if callable(semantic_reader):
                    semantic_context = semantic_reader(case_id)
                else:
                    # Legacy historical-case/v1 consumers without the additive
                    # semantic endpoint remain valid. Never fabricate typed
                    # semantics or evidence; explicitly mark generic-only.
                    semantic_context = {
                        "contract_version": REPEAT_RISK_CONTEXT_CONTRACT_VERSION,
                        "case_id": case_id,
                        "semantic_mode": "LEGACY_GENERIC_ONLY",
                        "semantic_contract_version": None,
                        "typed_causes": [],
                        "typed_actions": [],
                        "semantic_coverage": {},
                        "semantic_evidence_status": "LEGACY_GENERIC_ONLY",
                    }
                semantic_projection = self._semantic_projection(semantic_context, case_id)
            except HistoricalCaseContractError as exc:
                candidate["detail_status"] = SEARCH_INCOMPLETE
                candidate["detail_error"] = exc.code
                candidate.update(self._incomplete_semantic_projection())
                candidates.append(candidate)
                incomplete = True
                continue
            except Exception:
                candidate["detail_status"] = SEARCH_INCOMPLETE
                candidate["detail_error"] = "CASE_SEMANTIC_CONTEXT_UNAVAILABLE"
                candidate.update(self._incomplete_semantic_projection())
                candidates.append(candidate)
                incomplete = True
                continue

            try:
                detail = self.case_client.get_case(case_id)
            except HistoricalCaseContractError as exc:
                candidate["detail_status"] = SEARCH_INCOMPLETE
                candidate["detail_error"] = exc.code
                candidate.update(self._incomplete_semantic_projection())
                candidates.append(candidate)
                incomplete = True
                continue
            except Exception:
                candidate["detail_status"] = SEARCH_INCOMPLETE
                candidate["detail_error"] = "CASE_SERVICE_UNAVAILABLE"
                candidate.update(self._incomplete_semantic_projection())
                candidates.append(candidate)
                incomplete = True
                continue

            if not isinstance(detail, dict) or detail.get("contract_version") != HISTORICAL_CASE_CONTRACT_VERSION:
                candidate["detail_status"] = SEARCH_INCOMPLETE
                candidate["detail_error"] = "CASE_CONTRACT_INVALID"
                candidate.update(self._incomplete_semantic_projection())
                candidates.append(candidate)
                incomplete = True
                continue

            candidate.update(self._detail_projection(detail))
            candidate.update(semantic_projection)
            candidate["detail_status"] = SEARCH_SUCCESS
            candidate["detail_error"] = None
            candidates.append(candidate)

        base["candidates"] = candidates
        base["status"] = SEARCH_INCOMPLETE if incomplete else SEARCH_SUCCESS
        if incomplete:
            base["error_code"] = "CANDIDATE_DETAIL_INCOMPLETE"
        return base

    @staticmethod
    def _search_error_status(code: str) -> str:
        if code == "CASE_SERVICE_UNAVAILABLE":
            return SEARCH_UNAVAILABLE
        return SEARCH_INCOMPLETE

    @staticmethod
    def _to_query_input(trace: dict[str, Any]) -> QueryInput:
        snapshot = trace.get("itr_snapshot") or {}
        existing = snapshot.get("existing_context") or {}
        optional = trace.get("optional_context") or {}
        missed_snapshot = optional.get("snapshot") or {}

        primary_text = _text(snapshot.get("problem_description"))
        scene = _text(snapshot.get("scene"))
        text = "\n".join(part for part in (primary_text, scene) if part)

        cause_parts = _collect(
            existing,
            "root_cause",
            "cause_description",
            "occurrence_cause",
            "technical_cause",
            "management_cause",
        )
        if trace.get("include_missed_test"):
            for item in _collect(
                missed_snapshot,
                "description",
                "problem_description",
                "test_gap",
                "escape_cause",
                "root_cause",
            ):
                if item not in cause_parts:
                    cause_parts.append(item)

        solution_parts = _collect(
            existing,
            "solution",
            "action",
            "actions",
            "measure",
            "measures",
            "corrective_action",
        )

        return QueryInput(
            text=text,
            cause_description="\n".join(dict.fromkeys(cause_parts)),
            solution="\n".join(dict.fromkeys(solution_parts)),
            ipmt=_text(snapshot.get("ipmt")),
            spdt=_text(snapshot.get("spdt")),
            responsible_department_level2=_text(
                snapshot.get("responsible_department_level2")
            ),
            product=_text(snapshot.get("product")),
            domain=_text(existing.get("domain")),
            cause_level1=_text(snapshot.get("cause_level1")),
            cause_level2=_text(snapshot.get("cause_level2")),
        )

    @staticmethod
    def _base_candidate(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "case_id": item.get("case_id"),
            "title": item.get("title"),
            "summary": item.get("summary"),
            "retrieval_score": item.get("score"),
            "rank": item.get("rank"),
            "retrieval_reason": deepcopy(item.get("retrieval_reason") or []),
            "matched_fields": deepcopy(item.get("matched_fields") or []),
        }

    @staticmethod
    def _incomplete_semantic_projection() -> dict[str, Any]:
        return {
            "semantic_mode": "INCOMPLETE",
            "semantic_contract_version": None,
            "typed_causes": [],
            "typed_actions": [],
            "semantic_coverage": {},
            "semantic_evidence_status": "INCOMPLETE",
        }

    @staticmethod
    def _semantic_projection(context: Any, case_id: str) -> dict[str, Any]:
        if (
            not isinstance(context, dict)
            or context.get("contract_version") != REPEAT_RISK_CONTEXT_CONTRACT_VERSION
            or context.get("case_id") != case_id
        ):
            raise HistoricalCaseContractError("CASE_SEMANTIC_CONTRACT_INVALID")
        mode = context.get("semantic_mode")
        causes = context.get("typed_causes")
        actions = context.get("typed_actions")
        coverage = context.get("semantic_coverage")
        evidence_status = context.get("semantic_evidence_status")
        if (
            not isinstance(mode, str)
            or mode not in {"TYPED", "LEGACY_GENERIC_ONLY"}
            or not isinstance(causes, list)
            or not isinstance(actions, list)
            or not isinstance(coverage, dict)
            or not isinstance(evidence_status, str)
        ):
            raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")

        if mode == "LEGACY_GENERIC_ONLY":
            if causes or actions or context.get("semantic_contract_version") is not None:
                raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
            return {
                "semantic_mode": mode,
                "semantic_contract_version": None,
                "typed_causes": [],
                "typed_actions": [],
                "semantic_coverage": {
                    entry_type: "LEGACY_GENERIC_ONLY" for entry_type in REPEAT_SEMANTIC_TYPES
                },
                "semantic_evidence_status": "LEGACY_GENERIC_ONLY",
            }

        if context.get("semantic_contract_version") != SEMANTIC_PROJECTION_CONTRACT_VERSION:
            raise HistoricalCaseContractError("CASE_SEMANTIC_CONTRACT_INVALID")
        if set(coverage) != set(REPEAT_SEMANTIC_TYPES):
            raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
        if any(
            not isinstance(value, str) or value not in {"PRESENT", "MISSING"}
            for value in coverage.values()
        ):
            raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
        typed_items = causes + actions
        if any(not isinstance(item, dict) for item in typed_items):
            raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
        cause_types = set(REPEAT_SEMANTIC_TYPES[:4])
        action_types = set(REPEAT_SEMANTIC_TYPES[4:])
        if any(
            not isinstance(item.get("semantic_type"), str)
            or item.get("semantic_type") not in cause_types for item in causes
        ) or any(
            not isinstance(item.get("semantic_type"), str)
            or item.get("semantic_type") not in action_types for item in actions
        ):
            raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
        if any(
            not isinstance(item, dict)
            or not isinstance(item.get("semantic_type"), str)
            or item.get("semantic_type") not in REPEAT_SEMANTIC_TYPES
            or not isinstance(item.get("value"), str)
            or not item.get("value", "").strip()
            or not isinstance(item.get("evidence"), list)
            or not item.get("evidence")
            or (
                item.get("source_type") is not None
                and (
                    not isinstance(item.get("source_type"), str)
                    or item.get("source_type") not in {"EXCEL", "PDF", "FUSED"}
                )
            )
            for item in typed_items
        ):
            raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
        by_type: dict[str, int] = {}
        for item in typed_items:
            by_type[item["semantic_type"]] = by_type.get(item["semantic_type"], 0) + 1
            if any(not isinstance(evidence, dict) for evidence in item["evidence"]):
                raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
        if any(by_type.get(entry_type, 0) != 1 for entry_type in cause_types if coverage[entry_type] == "PRESENT"):
            raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
        if any(by_type.get(entry_type, 0) for entry_type in cause_types if coverage[entry_type] == "MISSING"):
            raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
        if any((by_type.get(entry_type, 0) > 0) != (coverage[entry_type] == "PRESENT") for entry_type in action_types):
            raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
        if evidence_status not in {"COMPLETE", "NO_TYPED_SEMANTICS"}:
            raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
        if evidence_status != ("COMPLETE" if typed_items else "NO_TYPED_SEMANTICS"):
            raise HistoricalCaseContractError("CASE_SEMANTIC_PROJECTION_INVALID")
        return {
            "semantic_mode": mode,
            "semantic_contract_version": SEMANTIC_PROJECTION_CONTRACT_VERSION,
            "typed_causes": deepcopy(causes),
            "typed_actions": deepcopy(actions),
            "semantic_coverage": deepcopy(coverage),
            "semantic_evidence_status": evidence_status,
        }

    @staticmethod
    def _detail_projection(detail: dict[str, Any]) -> dict[str, Any]:
        root_cause = _text(detail.get("root_cause"))
        solution = _text(detail.get("solution"))
        evidence = deepcopy(detail.get("evidence") or [])
        evidence_refs: list[dict[str, Any]] = []
        source_refs: list[str] = []

        for item in evidence:
            if not isinstance(item, dict):
                continue
            source_id = _text(item.get("source_id"))
            if source_id and source_id not in source_refs:
                source_refs.append(source_id)
            evidence_refs.append(
                {
                    "source_type": item.get("source_type"),
                    "source_id": item.get("source_id"),
                    "file_name": item.get("file_name"),
                    "page": item.get("page"),
                    "section": item.get("section"),
                    "url": item.get("url"),
                }
            )

        return {
            "historical_phenomenon": detail.get("symptom")
            or detail.get("problem_description"),
            "problem_description": detail.get("problem_description"),
            "product": detail.get("product"),
            "root_causes": [root_cause] if root_cause else [],
            "measures": [solution] if solution else [],
            "verification": detail.get("verification_result"),
            "evidence_refs": evidence_refs,
            "evidence": evidence,
            "source_ref": source_refs[0] if source_refs else None,
            "source_refs": source_refs,
            "case_status": detail.get("status"),
        }
