"""P04 provider port and relation adapter.

The default provider is intentionally unavailable.  Real provider JSON can be
connected later without changing the P04 consumer contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .contracts import P04State, RelationKind, RelationResolution, as_public_record


@dataclass(frozen=True)
class ProviderSnapshot:
    scenarios: tuple[dict[str, Any], ...] = ()
    result_revision: str = "unavailable"
    state: P04State = P04State.NORMAL
    warnings: tuple[str, ...] = ()


class P04Provider(Protocol):
    def snapshot(self) -> ProviderSnapshot:
        """Return a read-only, provider-neutral snapshot."""


class UnavailableP04Provider:
    """Production-safe placeholder until public JSON providers are wired."""

    def snapshot(self) -> ProviderSnapshot:
        return ProviderSnapshot(
            result_revision="provider-unavailable",
            state=P04State.DATA_UNAVAILABLE,
            warnings=("PENDING_PROVIDER_CONTRACT",),
        )


@dataclass
class RelationBundle:
    product: RelationResolution
    customer: RelationResolution
    industry: RelationResolution
    ipmt: RelationResolution
    spdt: RelationResolution

    def for_kind(self, kind: RelationKind) -> RelationResolution:
        return {
            RelationKind.SOURCE_PROBLEM_TO_PRODUCT: self.product,
            RelationKind.SOURCE_PROBLEM_TO_CUSTOMER: self.customer,
            RelationKind.CUSTOMER_TO_INDUSTRY: self.industry,
            RelationKind.PRODUCT_TO_IPMT: self.ipmt,
            RelationKind.PRODUCT_TO_SPDT: self.spdt,
        }[kind]


class P04RelationAdapter:
    """Normalize provider records into the five approved relation paths."""

    def resolve(self, raw: Any) -> RelationBundle:
        item = as_public_record(raw)
        product = self._value(item, "product", "product_code", "product_name")
        customer = self._value(item, "customer", "customer_name", "customer_id")
        industry = self._value(item, "industry", "industry_name", "industry_code")
        ipmt_code = self._value(item, "ipmt_code", "ipmt_id")
        ipmt = self._value(item, "ipmt", "ipmt_name")
        spdt = self._value(item, "spdt", "spdt_name")
        spdt_parent = self._value(item, "spdt_ipmt", "spdt_parent_ipmt")

        product_result = self._relation(
            RelationKind.SOURCE_PROBLEM_TO_PRODUCT, product,
        )
        customer_result = self._relation(
            RelationKind.SOURCE_PROBLEM_TO_CUSTOMER, customer,
        )
        industry_result = self._relation(
            RelationKind.CUSTOMER_TO_INDUSTRY, industry,
        )
        ipmt_result = self._relation(RelationKind.PRODUCT_TO_IPMT, ipmt)
        spdt_status = P04State.NORMAL if spdt else P04State.NO_RELATION_MAPPING
        spdt_warning = None
        if spdt and not ipmt:
            spdt_status = P04State.PARTIAL_DATA
            spdt_warning = "SPDT_REQUIRES_IPMT_PARENT"
        elif spdt and spdt_parent and spdt_parent not in {ipmt, ipmt_code}:
            spdt_status = P04State.PARTIAL_DATA
            spdt_warning = "SPDT_IPMT_PARENT_MISMATCH"
        spdt_result = RelationResolution(
            relation=RelationKind.PRODUCT_TO_SPDT,
            status=spdt_status,
            value=spdt or None,
            parent_value=ipmt or ipmt_code or None,
            matched=spdt_status == P04State.NORMAL,
            warning=spdt_warning,
        )
        return RelationBundle(
            product=product_result,
            customer=customer_result,
            industry=industry_result,
            ipmt=ipmt_result,
            spdt=spdt_result,
        )

    @staticmethod
    def _value(item: dict[str, Any], *names: str) -> str:
        for name in names:
            value = item.get(name)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    @staticmethod
    def _relation(kind: RelationKind, value: str) -> RelationResolution:
        return RelationResolution(
            relation=kind,
            status=P04State.NORMAL if value else P04State.NO_RELATION_MAPPING,
            value=value or None,
            matched=bool(value),
        )


def published_records(snapshot: ProviderSnapshot) -> tuple[dict[str, Any], ...]:
    """Keep the P04 source set limited to published QualityScenario records."""

    if snapshot.state not in {P04State.NORMAL, P04State.PARTIAL_DATA, P04State.NO_RELATION_MAPPING}:
        return ()
    return tuple(
        item
        for item in (as_public_record(row) for row in snapshot.scenarios)
        if str(item.get("status", "PUBLISHED")).upper() == "PUBLISHED"
    )
