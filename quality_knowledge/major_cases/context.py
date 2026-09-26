"""Public major-problem context projection.

This module is intentionally a DTO/projection boundary. It contains no
database access and does not expose the major knowledge repository or source
gateway to callers.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .service import MajorCaseService


CONTRACT_VERSION = "major-problem-context/v1"


class ContextRefV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = None
    name: str | None = None


class ProductV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_code: str | None = None
    product_name: str | None = None


class CustomerV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str | None = None
    customer_name: str | None = None


class IndustryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    industry_code: str | None = None
    industry_name: str | None = None


class OrganizationV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ipmt: ContextRefV1
    spdt: ContextRefV1


class MajorProblemContextV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    problem_id: str
    product: ProductV1
    customer: CustomerV1
    industry: IndustryV1
    organization: OrganizationV1
    relation_status: Literal["OBSERVED_IN_PROBLEM_EVIDENCE"] = "OBSERVED_IN_PROBLEM_EVIDENCE"
    source_refs: list[str]


def _first_value(evidence: list[dict[str, Any]], *keys: str) -> str | None:
    for item in evidence:
        sources = [item.get("raw"), item]
        for source in sources:
            if not isinstance(source, dict):
                continue
            for key in keys:
                value = source.get(key)
                if value is not None and str(value).strip():
                    return str(value).strip()
    return None


def _ref(evidence: list[dict[str, Any]], code_keys: tuple[str, ...], name_keys: tuple[str, ...]) -> ContextRefV1:
    return ContextRefV1(
        code=_first_value(evidence, *code_keys),
        name=_first_value(evidence, *name_keys),
    )


class MajorProblemContextProjection:
    """Map confirmed existing source facts to ``major-problem-context/v1``."""

    def __init__(self, service: MajorCaseService):
        self._service = service

    def project(self, problem_id: str) -> dict[str, Any] | None:
        facts = self._service.problem_context_facts(problem_id)
        if not facts:
            return None
        evidence = facts["evidence"]
        product = ProductV1(
            product_code=_first_value(evidence, "product_code", "产品编码", "产品代码"),
            product_name=_first_value(evidence, "product_name", "product", "产品名称", "问题信息_产品型号", "产品型号", "问题信息_产品类型", "产品类型"),
        )
        customer = CustomerV1(
            customer_id=_first_value(evidence, "customer_id", "客户编码", "客户代码"),
            customer_name=_first_value(evidence, "customer_name", "问题信息_客户名称", "客户名称"),
        )
        industry = IndustryV1(
            industry_code=_first_value(evidence, "industry_code", "行业编码", "行业代码"),
            industry_name=_first_value(evidence, "industry_name", "问题信息_客户行业", "客户行业"),
        )
        organization = OrganizationV1(
            ipmt=_ref(evidence, ("ipmt_code", "IPMT_CODE"), ("问题信息_IPMT", "IPMT")),
            spdt=_ref(evidence, ("spdt_code", "SPDT_CODE"), ("问题信息_SPDT", "SPDT")),
        )
        return MajorProblemContextV1(
            problem_id=facts["problem_id"],
            product=product,
            customer=customer,
            industry=industry,
            organization=organization,
            source_refs=facts["source_refs"],
        ).model_dump(mode="json")
