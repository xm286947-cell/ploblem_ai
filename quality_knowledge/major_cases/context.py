"""Main-line provider projection for the public major-problem context contract."""

from __future__ import annotations

import json
from typing import Any, Protocol


CONTRACT_VERSION = "major-problem-context/v1"


class MajorProblemContextProvider(Protocol):
    def get_context(self, problem_id: str) -> dict[str, Any] | None:
        """Return only the public context projection for one problem."""


class UnavailableMajorProblemContextProvider:
    def get_context(self, problem_id: str) -> dict[str, Any] | None:
        raise RuntimeError("MAJOR_CONTEXT_PROVIDER_UNAVAILABLE")


class RepositoryMajorProblemContextProvider:
    """Project explicit source facts without exposing the repository to P04."""

    def __init__(self, repository: Any) -> None:
        self.repository = repository

    def get_context(self, problem_id: str) -> dict[str, Any] | None:
        case_ref = self.repository.case_for_problem_id(problem_id)
        if not case_ref:
            return None
        case = self.repository.case_detail(case_ref["case_id"])
        if not case:
            return None

        contexts: list[dict[str, Any]] = []
        for link in case.get("source_links", []):
            if link.get("match_status") != "LINKED":
                continue
            try:
                payload = json.loads(link.get("snapshot_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                contexts.append(payload)

        payload = next((item for item in contexts if self._has_explicit_context(item)), {})
        product = self._object(payload.get("product"))
        customer = self._object(payload.get("customer"))
        industry = self._object(payload.get("industry"))
        organization = self._object(payload.get("organization"))
        ipmt = self._object(organization.get("ipmt"))
        spdt = self._object(organization.get("spdt"))
        return {
            "contract_version": CONTRACT_VERSION,
            "problem_id": problem_id,
            "product": self._public_object(product, "product_code", "product_name"),
            "customer": self._public_object(customer, "customer_id", "customer_name"),
            "industry": self._public_object(industry, "industry_code", "industry_name"),
            "organization": {
                "ipmt": self._public_object(ipmt, "code", "name"),
                "spdt": self._public_object(spdt, "code", "name"),
            },
            "relation_status": "OBSERVED_IN_PROBLEM_EVIDENCE",
            "source_refs": [problem_id],
        }

    @staticmethod
    def _object(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @classmethod
    def _has_explicit_context(cls, payload: dict[str, Any]) -> bool:
        return any(
            cls._object(payload.get(name))
            for name in ("product", "customer", "industry", "organization")
        )

    @staticmethod
    def _public_object(value: dict[str, Any], *allowed: str) -> dict[str, Any]:
        return {key: value.get(key) for key in allowed}
