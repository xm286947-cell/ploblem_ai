"""Public major-problem-context/v1 consumer for P04 real integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol

from .adapter import P04Provider, ProviderSnapshot
from .contracts import P04State


CONTRACT_VERSION = "major-problem-context/v1"


class MajorProblemContextClient(Protocol):
    def get_context(self, problem_id: str) -> tuple[int, dict[str, Any] | None]:
        """Return HTTP status and public JSON only."""


class CallableMajorProblemContextClient:
    def __init__(self, request: Callable[[str], Any]) -> None:
        self._request = request

    def get_context(self, problem_id: str) -> tuple[int, dict[str, Any] | None]:
        response = self._request(problem_id)
        status = int(getattr(response, "status_code", 200))
        if status == 404:
            return status, None
        if status != 200:
            return status, None
        payload = response.json() if hasattr(response, "json") else response
        return status, payload if isinstance(payload, dict) else None


@dataclass(frozen=True)
class ContextEnrichment:
    record: dict[str, Any]
    status: P04State
    warnings: tuple[str, ...] = ()


class QualityScenarioMajorProblemContextAdapter:
    """Enrich published scenarios through the public HTTP contract only."""

    def __init__(self, client: MajorProblemContextClient) -> None:
        self.client = client
        self._cache: dict[tuple[str, str], tuple[int, dict[str, Any] | None]] = {}

    def enrich(self, scenario: dict[str, Any], *, result_revision: str) -> ContextEnrichment:
        record = dict(scenario)
        problem_ids = self._problem_ids(record)
        if not problem_ids:
            record["relation_status"] = "NO_RELATION_MAPPING"
            return ContextEnrichment(record, P04State.NO_RELATION_MAPPING, ("SOURCE_PROBLEM_MISSING",))

        contexts: list[dict[str, Any]] = []
        warnings: list[str] = []
        state = P04State.NORMAL
        for problem_id in problem_ids:
            key = (result_revision, problem_id)
            if key not in self._cache:
                self._cache[key] = self.client.get_context(problem_id)
            status, payload = self._cache[key]
            if status == 404:
                state = P04State.PARTIAL_DATA if contexts else P04State.NO_RELATION_MAPPING
                warnings.append("SOURCE_PROBLEM_NOT_FOUND")
                continue
            if status in {401, 403}:
                return ContextEnrichment(record, P04State.PERMISSION_UNAVAILABLE, ("MAJOR_CONTEXT_PERMISSION",))
            if status >= 500 or status == 0:
                return ContextEnrichment(
                    record,
                    P04State.PARTIAL_DATA if contexts else P04State.DATA_UNAVAILABLE,
                    ("MAJOR_CONTEXT_PROVIDER_UNAVAILABLE",),
                )
            if not payload or payload.get("contract_version") != CONTRACT_VERSION:
                return ContextEnrichment(record, P04State.ERROR, ("CONTRACT_VERSION_MISMATCH",))
            contexts.append(payload)

        if contexts:
            self._merge_context(record, contexts)
            if len(contexts) < len(problem_ids):
                state = P04State.PARTIAL_DATA
            record["relation_status"] = "OBSERVED_IN_PROBLEM_EVIDENCE"
            return ContextEnrichment(record, state, tuple(sorted(set(warnings))))
        record["relation_status"] = "NO_RELATION_MAPPING"
        return ContextEnrichment(record, P04State.NO_RELATION_MAPPING, tuple(sorted(set(warnings))))

    @staticmethod
    def _problem_ids(record: dict[str, Any]) -> list[str]:
        values = record.get("source_problem_ids") or record.get("source_problem_id") or []
        if isinstance(values, str):
            values = [values]
        return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))

    @staticmethod
    def _merge_context(record: dict[str, Any], contexts: Iterable[dict[str, Any]]) -> None:
        contexts = list(contexts)
        first = contexts[0]
        product = first.get("product") or {}
        customer = first.get("customer") or {}
        industry = first.get("industry") or {}
        organization = first.get("organization") or {}
        ipmt = organization.get("ipmt") or {}
        spdt = organization.get("spdt") or {}

        def choose(current: Any, *values: Any) -> Any:
            if current not in (None, ""):
                return current
            return next((value for value in values if value not in (None, "")), None)

        record["product_code"] = choose(record.get("product_code"), product.get("product_code"))
        record["product"] = choose(record.get("product"), product.get("product_name"), product.get("product_code"))
        record["customer_id"] = choose(record.get("customer_id"), customer.get("customer_id"))
        record["customer"] = choose(record.get("customer"), customer.get("customer_name"), customer.get("customer_id"))
        record["industry_code"] = choose(record.get("industry_code"), industry.get("industry_code"))
        record["industry"] = choose(record.get("industry"), industry.get("industry_name"), industry.get("industry_code"))
        record["ipmt_code"] = choose(record.get("ipmt_code"), ipmt.get("code"))
        record["ipmt"] = choose(record.get("ipmt"), ipmt.get("name"), ipmt.get("code"))
        record["spdt_code"] = choose(record.get("spdt_code"), spdt.get("code"))
        record["spdt"] = choose(record.get("spdt"), spdt.get("name"), spdt.get("code"))
        record["spdt_ipmt"] = choose(record.get("spdt_ipmt"), ipmt.get("code"))
        record["source_contract_version"] = CONTRACT_VERSION
        record["source_problem_id"] = first.get("problem_id")
        record["source_refs"] = list(dict.fromkeys(ref for context in contexts for ref in context.get("source_refs", [])))
        record["source_contexts"] = contexts


class IntegratedP04Provider:
    """P04 provider that consumes QualityScenario rows and public context JSON."""

    def __init__(self, source: P04Provider, client: MajorProblemContextClient) -> None:
        self.source = source
        self.adapter = QualityScenarioMajorProblemContextAdapter(client)

    def snapshot(self) -> ProviderSnapshot:
        source_snapshot = self.source.snapshot()
        if source_snapshot.state != P04State.NORMAL:
            return source_snapshot
        enriched: list[dict[str, Any]] = []
        warnings: set[str] = set(source_snapshot.warnings)
        states: list[P04State] = []
        for scenario in source_snapshot.scenarios:
            if str(scenario.get("status", "PUBLISHED")).upper() != "PUBLISHED":
                continue
            result = self.adapter.enrich(dict(scenario), result_revision=source_snapshot.result_revision)
            enriched.append(result.record)
            states.append(result.status)
            warnings.update(result.warnings)
        if any(state == P04State.ERROR for state in states):
            state = P04State.ERROR
        elif states and all(state == P04State.DATA_UNAVAILABLE for state in states):
            state = P04State.DATA_UNAVAILABLE
        elif states and all(state == P04State.NO_RELATION_MAPPING for state in states):
            state = P04State.NO_RELATION_MAPPING
        elif any(state in {P04State.DATA_UNAVAILABLE, P04State.PARTIAL_DATA, P04State.NO_RELATION_MAPPING} for state in states):
            state = P04State.PARTIAL_DATA
        else:
            state = P04State.NORMAL
        return ProviderSnapshot(
            scenarios=tuple(enriched),
            result_revision=source_snapshot.result_revision,
            state=state,
            warnings=tuple(sorted(warnings)),
        )


class IntegratedPortraitProvider:
    """Portrait port backed by the same enriched P04 snapshot."""

    def __init__(self, provider: IntegratedP04Provider) -> None:
        self.provider = provider

    def query(self, filters: dict[str, str]) -> tuple[list[dict[str, Any]], str]:
        snapshot = self.provider.snapshot()
        if snapshot.state in {P04State.DATA_UNAVAILABLE, P04State.ERROR, P04State.PERMISSION_UNAVAILABLE}:
            raise RuntimeError("MAJOR_CONTEXT_PROVIDER_UNAVAILABLE")
        rows = []
        for row in snapshot.scenarios:
            if all(str(row.get(key) or "") == value for key, value in filters.items()):
                rows.append(dict(row))
        return rows, snapshot.result_revision
