"""Public P04 consumer contract.

These models are the boundary between the P04 UI/API and an approved provider
adapter.  Provider-specific identifiers stay behind ``selector_ref`` and
``drilldown_query``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class P04State(str, Enum):
    NORMAL = "NORMAL"
    EMPTY = "EMPTY"
    NO_RELATION_MAPPING = "NO_RELATION_MAPPING"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    PARTIAL_DATA = "PARTIAL_DATA"
    PERMISSION_UNAVAILABLE = "PERMISSION_UNAVAILABLE"
    ERROR = "ERROR"


class P04View(str, Enum):
    PRODUCT = "PRODUCT"
    CUSTOMER = "CUSTOMER"
    INDUSTRY = "INDUSTRY"


class RelationKind(str, Enum):
    SOURCE_PROBLEM_TO_PRODUCT = "SOURCE_PROBLEM_TO_PRODUCT"
    SOURCE_PROBLEM_TO_CUSTOMER = "SOURCE_PROBLEM_TO_CUSTOMER"
    CUSTOMER_TO_INDUSTRY = "CUSTOMER_TO_INDUSTRY"
    PRODUCT_TO_IPMT = "PRODUCT_TO_IPMT"
    PRODUCT_TO_SPDT = "PRODUCT_TO_SPDT"


class RelationResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation: RelationKind
    status: P04State
    value: str | None = None
    parent_value: str | None = None
    matched: bool = False
    warning: str | None = None


class SelectorItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selector_ref: str
    view: P04View
    level: str
    label: str
    availability: P04State = P04State.NORMAL
    parent_selector_ref: str | None = None


class DrilldownQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_context_id: str
    result_revision: str
    view: P04View
    selected_object_ref: str | None = None
    filters: dict[str, str] = Field(default_factory=dict)
    matrix_mode: str | None = None
    selection: dict[str, str] = Field(default_factory=dict)


class StatCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_key: str
    value: int
    state: P04State
    drilldown_query: DrilldownQuery | None = None


class MatrixCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x_key: str
    y_key: str
    count: int
    state: P04State
    selection: dict[str, str] = Field(default_factory=dict)
    drilldown_query: DrilldownQuery


class Matrix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    x_dimension: str
    y_dimension: str
    rows: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    cells: list[MatrixCell] = Field(default_factory=list)
    state: P04State


class DistributionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    count: int
    coverage_count: int | None = None
    state: P04State
    drilldown_query: DrilldownQuery


class ScenarioListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    scenario_name: str
    lifecycle: str
    business_activity: str
    quality_focus: str
    trigger_summary: str
    failure_mode_summary: str
    source_problem_count: int
    product_context: str | None = None
    customer_context: str | None = None
    industry_context: str | None = None
    ipmt_context: str | None = None
    spdt_context: str | None = None
    p03_path: str


class Coverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_count: int
    source_problem_count: int
    product_count: int
    customer_count: int
    industry_count: int
    unmapped_scenario_count: int = 0


class QueryEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str = "quality-scenario-insight/v1"
    state: P04State
    view: P04View
    result_revision: str
    query_context_id: str
    selector_items: list[SelectorItem] = Field(default_factory=list)
    stat_cards: list[StatCard] = Field(default_factory=list)
    matrix: Matrix | None = None
    distributions: dict[str, list[DistributionItem]] = Field(default_factory=dict)
    scenario_list: list[ScenarioListItem] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20
    coverage: Coverage = Field(default_factory=lambda: Coverage(
        scenario_count=0,
        source_problem_count=0,
        product_count=0,
        customer_count=0,
        industry_count=0,
    ))
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None


class DrilldownResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str = "quality-scenario-insight/v1"
    state: P04State
    status: str = "OK"
    result_revision: str
    query_context_id: str
    scenario_ids: list[str] = Field(default_factory=list)
    scenario_list: list[ScenarioListItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None


def as_public_record(value: Any) -> dict[str, Any]:
    """Convert a provider record without leaking unknown provider objects."""

    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    return {"scenario_id": str(value)}
