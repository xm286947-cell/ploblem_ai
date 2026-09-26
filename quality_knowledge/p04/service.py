"""P04 aggregation and stable-drilldown service."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Iterable

from .adapter import P04Provider, P04RelationAdapter, ProviderSnapshot, published_records
from .contracts import (
    Coverage,
    DistributionItem,
    DrilldownQuery,
    DrilldownResult,
    Matrix,
    MatrixCell,
    P04State,
    P04View,
    QueryEnvelope,
    ScenarioListItem,
    SelectorItem,
    StatCard,
)


class P04InsightService:
    """Read-only P04 query service with an injectable provider port."""

    def __init__(self, provider: P04Provider) -> None:
        self.provider = provider
        self.relations = P04RelationAdapter()

    def selectors(self, view: P04View | str) -> dict[str, Any]:
        view = self._view(view)
        snapshot = self._snapshot()
        if self._is_terminal_state(snapshot.state):
            return {
                "contract_version": "quality-scenario-insight/v1",
                "state": snapshot.state,
                "items": [],
                "warnings": list(snapshot.warnings),
                "result_revision": snapshot.result_revision,
            }
        records = published_records(snapshot)
        seen: dict[tuple[str, str], SelectorItem] = {}
        for record in records:
            if record.get("relation_status") == P04State.NO_RELATION_MAPPING:
                continue
            item = self.relations.resolve(record)
            candidates = self._selector_values(view, record, item)
            for level, label in candidates:
                key = (level, label)
                if key not in seen:
                    seen[key] = SelectorItem(
                        selector_ref=self._selector_ref(view, level, label),
                        view=view,
                        level=level,
                        label=label,
                    )
        selector_state = P04State.EMPTY
        if seen:
            selector_state = (
                P04State.PARTIAL_DATA
                if snapshot.state == P04State.PARTIAL_DATA
                else P04State.NORMAL
            )
        elif snapshot.state == P04State.NO_RELATION_MAPPING:
            selector_state = P04State.NO_RELATION_MAPPING
        return {
            "contract_version": "quality-scenario-insight/v1",
            "state": selector_state,
            "items": list(sorted(seen.values(), key=lambda x: (x.level, x.label))),
            "warnings": list(snapshot.warnings),
            "result_revision": snapshot.result_revision,
        }

    def query(self, payload: dict[str, Any]) -> QueryEnvelope:
        view = self._view(payload.get("view", P04View.PRODUCT))
        snapshot = self._snapshot()
        context_id = self._context_id(payload, view)
        if self._is_terminal_state(snapshot.state):
            return self._empty_envelope(
                view,
                snapshot,
                context_id,
                state=snapshot.state,
                warnings=list(snapshot.warnings),
            )

        selectors = self.selectors(view)
        selector_map = {item.selector_ref: item for item in selectors["items"]}
        selected_ref = self._selected_ref(payload)
        selected = selector_map.get(selected_ref) if selected_ref else None
        if selected_ref and selected is None:
            return self._empty_envelope(
                view,
                snapshot,
                context_id,
                state=P04State.ERROR,
                warnings=["SELECTOR_NOT_FOUND"],
                error_code="SELECTOR_NOT_FOUND",
            )
        if selected and selected.view != view:
            return self._empty_envelope(
                view,
                snapshot,
                context_id,
                state=P04State.ERROR,
                warnings=["SELECTOR_VIEW_MISMATCH"],
                error_code="SELECTOR_VIEW_MISMATCH",
            )

        filters = self._filters(payload.get("filters"))
        if selected:
            filters[selected.level.lower()] = selected.label
        records = [r for r in published_records(snapshot) if self._matches(r, filters)]
        records, relation_state, warnings, unmapped = self._mapped_records(records, view)
        if not records:
            state = (
                P04State.NO_RELATION_MAPPING
                if unmapped or snapshot.state == P04State.NO_RELATION_MAPPING
                else P04State.EMPTY
            )
            return self._empty_envelope(
                view,
                snapshot,
                context_id,
                state=state,
                warnings=list(snapshot.warnings) + warnings,
                error_code=None,
                unmapped=unmapped,
            )

        mode = str(payload.get("matrix_mode") or "").upper() or self._default_matrix_mode(view)
        page = max(1, int(payload.get("page") or 1))
        page_size = min(100, max(1, int(payload.get("page_size") or 20)))
        query = self._query_object(
            snapshot=snapshot,
            context_id=context_id,
            view=view,
            selected_ref=selected_ref,
            filters=filters,
            matrix_mode=mode,
            selection={},
        )
        items = self._scenario_items(records)
        cards = self._stat_cards(view, records, query)
        matrix = self._matrix(view, mode, records, query)
        distributions = self._distributions(view, records, query)
        total = len(items)
        start = (page - 1) * page_size
        state = relation_state
        if state == P04State.NORMAL and snapshot.state == P04State.PARTIAL_DATA:
            state = P04State.PARTIAL_DATA
        coverage = self._coverage(records, unmapped)
        return QueryEnvelope(
            state=state,
            view=view,
            result_revision=snapshot.result_revision,
            query_context_id=context_id,
            selector_items=selectors["items"],
            stat_cards=cards,
            matrix=matrix,
            distributions=distributions,
            scenario_list=items[start : start + page_size],
            total=total,
            page=page,
            page_size=page_size,
            coverage=coverage,
            warnings=list(snapshot.warnings) + warnings,
        )

    def drilldown(self, payload: dict[str, Any]) -> DrilldownResult:
        view = self._view(payload.get("view", P04View.PRODUCT))
        snapshot = self._snapshot()
        context_id = str(payload.get("query_context_id") or "")
        if self._is_terminal_state(snapshot.state):
            return DrilldownResult(
                state=snapshot.state,
                status="UNAVAILABLE",
                result_revision=snapshot.result_revision,
                query_context_id=context_id,
                warnings=list(snapshot.warnings),
            )
        requested_revision = str(payload.get("result_revision") or "")
        if requested_revision != snapshot.result_revision:
            return DrilldownResult(
                state=P04State.ERROR,
                status="REVISION_CHANGED",
                result_revision=snapshot.result_revision,
                query_context_id=context_id,
                error_code="RESULT_REVISION_CHANGED",
                warnings=["REFRESH_REQUIRED"],
            )
        selection = self._filters(payload.get("selection"))
        base = {
            "view": view,
            "selected_object": {"selector_ref": payload.get("selected_object_ref")},
            "filters": {**self._filters(payload.get("filters")), **selection},
            "matrix_mode": payload.get("matrix_mode"),
            "page": 1,
            "page_size": 100,
        }
        result = self.query(base)
        items = result.scenario_list
        return DrilldownResult(
            state=result.state,
            status="OK",
            result_revision=result.result_revision,
            query_context_id=context_id or result.query_context_id,
            scenario_ids=[item.scenario_id for item in items],
            scenario_list=items,
            warnings=result.warnings,
            error_code=result.error_code,
        )

    def _snapshot(self) -> ProviderSnapshot:
        try:
            snapshot = self.provider.snapshot()
        except PermissionError:
            return ProviderSnapshot(state=P04State.PERMISSION_UNAVAILABLE, result_revision="permission")
        except Exception:
            return ProviderSnapshot(state=P04State.ERROR, result_revision="error", warnings=("PROVIDER_ERROR",))
        return snapshot

    @staticmethod
    def _is_terminal_state(state: P04State) -> bool:
        """States that prevent serving any provider-backed result."""

        return state in {
            P04State.DATA_UNAVAILABLE,
            P04State.PERMISSION_UNAVAILABLE,
            P04State.ERROR,
        }

    @staticmethod
    def _view(value: Any) -> P04View:
        try:
            return value if isinstance(value, P04View) else P04View(str(value).upper())
        except ValueError as error:
            raise ValueError("INVALID_VIEW") from error

    @staticmethod
    def _selected_ref(payload: dict[str, Any]) -> str | None:
        selected = payload.get("selected_object")
        if isinstance(selected, dict):
            return str(selected.get("selector_ref") or "") or None
        return str(payload.get("selected_object_ref") or "") or None

    @staticmethod
    def _filters(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {
            str(key).strip().lower(): str(item).strip()
            for key, item in value.items()
            if item is not None and str(item).strip()
        }

    @staticmethod
    def _matches(record: dict[str, Any], filters: dict[str, str]) -> bool:
        aliases = {
            "product": "product",
            "product_family": "product_family",
            "customer": "customer",
            "industry": "industry",
            "lifecycle": "lifecycle",
            "business_activity": "business_activity",
            "quality_focus": "quality_focus",
            "failure_mode": "failure_mode",
            "ipmt": "ipmt",
            "spdt": "spdt",
        }
        return all(str(record.get(aliases.get(k, k)) or "") == v for k, v in filters.items())

    def _mapped_records(
        self,
        records: list[dict[str, Any]],
        view: P04View,
    ) -> tuple[list[dict[str, Any]], P04State, list[str], int]:
        mapped: list[dict[str, Any]] = []
        partial = False
        unmapped = 0
        warnings: list[str] = []
        for record in records:
            if record.get("relation_status") == P04State.NO_RELATION_MAPPING:
                unmapped += 1
                continue
            relations = self.relations.resolve(record)
            if relations.spdt.status == P04State.PARTIAL_DATA:
                partial = True
                if relations.spdt.warning:
                    warnings.append(relations.spdt.warning)
            required = {
                P04View.PRODUCT: (relations.product,),
                P04View.CUSTOMER: (relations.product, relations.customer),
                P04View.INDUSTRY: (relations.product, relations.customer, relations.industry),
            }[view]
            statuses = {item.status for item in required}
            if all(item.status == P04State.NORMAL for item in required):
                mapped.append(record)
            elif P04State.PARTIAL_DATA in statuses:
                partial = True
                unmapped += 1
                warnings.extend(item.warning for item in required if item.warning)
            else:
                unmapped += 1
        if mapped and (partial or unmapped):
            return mapped, P04State.PARTIAL_DATA, sorted(set(warnings + ["UNMAPPED_SCENARIO_PRESENT"])), unmapped
        return mapped, P04State.NORMAL, sorted(set(warnings)), unmapped

    @staticmethod
    def _selector_values(view: P04View, record: dict[str, Any], relations: Any) -> list[tuple[str, str]]:
        if view == P04View.PRODUCT:
            values = [("PRODUCT", relations.product.value)]
            if record.get("product_family"):
                values.append(("PRODUCT_FAMILY", str(record["product_family"])))
            return [(level, value) for level, value in values if value]
        if view == P04View.CUSTOMER:
            return [("CUSTOMER", relations.customer.value)] if relations.customer.value else []
        return [("INDUSTRY", relations.industry.value)] if relations.industry.value else []

    @staticmethod
    def _selector_ref(view: P04View, level: str, label: str) -> str:
        token = hashlib.sha256(f"{view.value}|{level}|{label}".encode("utf-8")).hexdigest()[:24]
        return f"selector_{token}"

    @staticmethod
    def _context_id(payload: dict[str, Any], view: P04View) -> str:
        material = {
            "view": view.value,
            "selected_object": payload.get("selected_object") or payload.get("selected_object_ref"),
            "filters": payload.get("filters") or {},
            "matrix_mode": payload.get("matrix_mode") or "",
        }
        return "ctx_" + hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]

    @staticmethod
    def _default_matrix_mode(view: P04View) -> str:
        return {
            P04View.PRODUCT: "LIFECYCLE_X_BUSINESS_ACTIVITY",
            P04View.CUSTOMER: "PRODUCT_X_BUSINESS_ACTIVITY",
            P04View.INDUSTRY: "CUSTOMER_X_PRODUCT_OR_FAMILY",
        }[view]

    @staticmethod
    def _empty_envelope(
        view: P04View,
        snapshot: ProviderSnapshot,
        context_id: str,
        *,
        state: P04State,
        warnings: list[str],
        error_code: str | None = None,
        unmapped: int = 0,
    ) -> QueryEnvelope:
        return QueryEnvelope(
            state=state,
            view=view,
            result_revision=snapshot.result_revision,
            query_context_id=context_id,
            warnings=warnings,
            error_code=error_code,
            coverage=Coverage(
                scenario_count=0,
                source_problem_count=0,
                product_count=0,
                customer_count=0,
                industry_count=0,
                unmapped_scenario_count=unmapped,
            ),
        )

    @staticmethod
    def _query_object(
        *,
        snapshot: ProviderSnapshot,
        context_id: str,
        view: P04View,
        selected_ref: str | None,
        filters: dict[str, str],
        matrix_mode: str | None,
        selection: dict[str, str],
    ) -> DrilldownQuery:
        return DrilldownQuery(
            query_context_id=context_id,
            result_revision=snapshot.result_revision,
            view=view,
            selected_object_ref=selected_ref,
            filters=filters,
            matrix_mode=matrix_mode,
            selection=selection,
        )

    @staticmethod
    def _scenario_items(records: Iterable[dict[str, Any]]) -> list[ScenarioListItem]:
        items = []
        for record in sorted(records, key=lambda x: str(x.get("scenario_id") or "")):
            sources = record.get("source_problem_ids") or []
            if not isinstance(sources, list):
                sources = [sources]
            items.append(ScenarioListItem(
                scenario_id=str(record.get("scenario_id") or ""),
                scenario_name=str(record.get("scenario_name") or record.get("scenario_id") or ""),
                lifecycle=str(record.get("lifecycle") or ""),
                business_activity=str(record.get("business_activity") or ""),
                quality_focus=str(record.get("quality_focus") or ""),
                trigger_summary=str(record.get("trigger_summary") or ""),
                failure_mode_summary=str(record.get("failure_mode_summary") or record.get("failure_mode") or ""),
                source_problem_count=len({str(item) for item in sources if str(item)}),
                product_context=str(record.get("product") or record.get("product_family") or "") or None,
                customer_context=str(record.get("customer") or "") or None,
                industry_context=str(record.get("industry") or "") or None,
                ipmt_context=str(record.get("ipmt") or "") or None,
                spdt_context=str(record.get("spdt") or "") or None,
                p03_path=f"/p0/issues/{record.get('scenario_id')}",
            ))
        return items

    @staticmethod
    def _unique(records: Iterable[dict[str, Any]], key: str) -> set[str]:
        return {str(item.get(key)) for item in records if item.get(key) not in (None, "")}

    def _coverage(self, records: list[dict[str, Any]], unmapped: int) -> Coverage:
        source_ids = set()
        for record in records:
            values = record.get("source_problem_ids") or []
            if not isinstance(values, list):
                values = [values]
            source_ids.update(str(value) for value in values if str(value))
        return Coverage(
            scenario_count=len({str(item.get("scenario_id")) for item in records}),
            source_problem_count=len(source_ids),
            product_count=len(self._unique(records, "product")),
            customer_count=len(self._unique(records, "customer")),
            industry_count=len(self._unique(records, "industry")),
            unmapped_scenario_count=unmapped,
        )

    def _stat_cards(self, view: P04View, records: list[dict[str, Any]], query: DrilldownQuery) -> list[StatCard]:
        coverage = self._coverage(records, 0)
        keys = {
            P04View.PRODUCT: [
                ("PUBLISHED_SCENARIO_COUNT", coverage.scenario_count),
                ("SOURCE_PROBLEM_COUNT", coverage.source_problem_count),
                ("CUSTOMER_COVERAGE_COUNT", coverage.customer_count),
                ("QUALITY_FOCUS_TYPE_COUNT", len(self._unique(records, "quality_focus"))),
            ],
            P04View.CUSTOMER: [
                ("PUBLISHED_SCENARIO_COUNT", coverage.scenario_count),
                ("PRODUCT_COVERAGE_COUNT", coverage.product_count),
                ("SOURCE_PROBLEM_COUNT", coverage.source_problem_count),
                ("QUALITY_FOCUS_TYPE_COUNT", len(self._unique(records, "quality_focus"))),
            ],
            P04View.INDUSTRY: [
                ("PUBLISHED_SCENARIO_COUNT", coverage.scenario_count),
                ("CUSTOMER_COVERAGE_COUNT", coverage.customer_count),
                ("PRODUCT_OR_FAMILY_COVERAGE_COUNT", coverage.product_count),
                ("SOURCE_PROBLEM_COUNT", coverage.source_problem_count),
            ],
        }[view]
        return [StatCard(metric_key=key, value=value, state=P04State.NORMAL, drilldown_query=query) for key, value in keys]

    def _matrix(self, view: P04View, mode: str, records: list[dict[str, Any]], query: DrilldownQuery) -> Matrix:
        dimensions = {
            "LIFECYCLE_X_BUSINESS_ACTIVITY": ("LIFECYCLE", "BUSINESS_ACTIVITY", "lifecycle", "business_activity"),
            "PRODUCT_X_BUSINESS_ACTIVITY": ("PRODUCT", "BUSINESS_ACTIVITY", "product", "business_activity"),
            "PRODUCT_X_QUALITY_FOCUS": ("PRODUCT", "QUALITY_FOCUS", "product", "quality_focus"),
            "CUSTOMER_X_PRODUCT_OR_FAMILY": ("CUSTOMER", "PRODUCT_OR_FAMILY", "customer", "product"),
            "BUSINESS_ACTIVITY_X_QUALITY_FOCUS": ("BUSINESS_ACTIVITY", "QUALITY_FOCUS", "business_activity", "quality_focus"),
        }
        x_dim, y_dim, x_key, y_key = dimensions.get(mode, dimensions[self._default_matrix_mode(view)])
        rows = sorted({str(record.get(x_key) or "") for record in records if record.get(x_key)})
        columns = sorted({str(record.get(y_key) or "") for record in records if record.get(y_key)})
        cells: list[MatrixCell] = []
        for x in rows:
            for y in columns:
                selected = [r for r in records if str(r.get(x_key) or "") == x and str(r.get(y_key) or "") == y]
                if not selected:
                    continue
                cell_query = query.model_copy(update={"selection": {x_key: x, y_key: y}})
                cells.append(MatrixCell(
                    x_key=x,
                    y_key=y,
                    count=len({str(r.get("scenario_id")) for r in selected}),
                    state=P04State.NORMAL,
                    selection={x_key: x, y_key: y},
                    drilldown_query=cell_query,
                ))
        return Matrix(
            mode=mode,
            x_dimension=x_dim,
            y_dimension=y_dim,
            rows=rows,
            columns=columns,
            cells=cells,
            state=P04State.NORMAL if cells else P04State.EMPTY,
        )

    def _distributions(self, view: P04View, records: list[dict[str, Any]], query: DrilldownQuery) -> dict[str, list[DistributionItem]]:
        fields = {
            P04View.PRODUCT: [("QUALITY_FOCUS", "quality_focus"), ("FAILURE_MODE", "failure_mode"), ("OPERATING_CONDITION_OR_TRIGGER", "trigger_summary"), ("CUSTOMER_COVERAGE", "customer")],
            P04View.CUSTOMER: [("PRODUCT_COVERAGE", "product"), ("QUALITY_FOCUS", "quality_focus"), ("CROSS_PRODUCT_COMMON_QUALITY_FOCUS", "quality_focus"), ("OPERATING_CONDITION", "trigger_summary"), ("FAILURE_MODE", "failure_mode")],
            P04View.INDUSTRY: [("CUSTOMER_COVERAGE", "customer"), ("PRODUCT_OR_FAMILY_COVERAGE", "product"), ("BUSINESS_ACTIVITY", "business_activity"), ("QUALITY_FOCUS", "quality_focus"), ("OPERATING_CONDITION", "trigger_summary"), ("FAILURE_MODE", "failure_mode")],
        }[view]
        result: dict[str, list[DistributionItem]] = {}
        for name, key in fields:
            groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for record in records:
                value = str(record.get(key) or "")
                if value:
                    groups[value].append(record)
            result[name] = []
            for value, grouped in sorted(groups.items()):
                result[name].append(DistributionItem(
                    key=value,
                    label=value,
                    count=len({str(r.get("scenario_id")) for r in grouped}),
                    coverage_count=len({str(r.get(key)) for r in grouped}),
                    state=P04State.NORMAL,
                    drilldown_query=query.model_copy(update={"selection": {key: value}}),
                ))
        return result
