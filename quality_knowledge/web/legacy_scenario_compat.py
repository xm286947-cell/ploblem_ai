from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates


class LegacyScenarioReadRepository:
    """Read-only compatibility view over historical scenario tables.

    The adapter never creates, alters, inserts, updates or deletes legacy data.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def tables(self) -> set[str]:
        try:
            with self.connect() as connection:
                return {
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
        except sqlite3.Error:
            return set()

    def status(self) -> dict[str, Any]:
        tables = self.tables()
        ready = "quality_scenario" in tables
        return {
            "ready": ready,
            "code": "READY" if ready else "LEGACY_SCENARIO_TABLE_NOT_FOUND",
            "database_path": str(self.db_path),
            "mode": "READ_ONLY",
        }

    def _columns(self, connection: sqlite3.Connection, table: str) -> set[str]:
        return {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})")
        }

    def scenarios(
        self,
        *,
        q: str = "",
        status: str = "",
        product_code: str = "",
    ) -> list[dict[str, Any]]:
        if not self.status()["ready"]:
            return []
        query = str(q or "").strip().lower()
        status_filter = str(status or "").strip().upper()
        product_filter = str(product_code or "").strip()
        with self.connect() as connection:
            columns = self._columns(connection, "quality_scenario")
            preferred = (
                "scenario_id",
                "scenario_code",
                "name",
                "product_code",
                "lifecycle_code",
                "activity_code",
                "status",
                "version_no",
                "updated_at",
            )
            selected = [name for name in preferred if name in columns]
            rows = [
                dict(row)
                for row in connection.execute(
                    "SELECT " + ",".join(selected) + " FROM quality_scenario ORDER BY updated_at DESC"
                    if "updated_at" in columns
                    else "SELECT " + ",".join(selected) + " FROM quality_scenario"
                )
            ]
            scope_map: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
            if "quality_scenario_scope" in self.tables():
                for row in connection.execute(
                    "SELECT scenario_id,scope_type,scope_value FROM quality_scenario_scope"
                ):
                    scope_map[row["scenario_id"]][row["scope_type"]].append(row["scope_value"])
            evidence_count: Counter[str] = Counter()
            if "quality_scenario_evidence" in self.tables():
                for row in connection.execute(
                    "SELECT scenario_id,COUNT(*) AS count FROM quality_scenario_evidence GROUP BY scenario_id"
                ):
                    evidence_count[row["scenario_id"]] = int(row["count"])
        result = []
        for row in rows:
            item = {
                **row,
                "scopes": dict(scope_map.get(row.get("scenario_id"), {})),
                "evidence_count": evidence_count.get(row.get("scenario_id"), 0),
            }
            searchable = " ".join(
                str(item.get(key) or "")
                for key in ("scenario_code", "name", "product_code", "lifecycle_code", "activity_code")
            ).lower()
            if query and query not in searchable:
                continue
            if status_filter and str(item.get("status") or "").upper() != status_filter:
                continue
            if product_filter and str(item.get("product_code") or "") != product_filter:
                continue
            result.append(item)
        return result

    def scenario(self, scenario_id: str) -> dict[str, Any] | None:
        return next(
            (item for item in self.scenarios() if item.get("scenario_id") == scenario_id),
            None,
        )

    def portrait(self, scenarios: list[dict[str, Any]]) -> dict[str, Any]:
        def values(item: dict[str, Any], *keys: str) -> list[str]:
            scopes = item.get("scopes") or {}
            result: list[str] = []
            for key in keys:
                result.extend(str(value) for value in scopes.get(key, []) if str(value).strip())
            return result

        product = Counter()
        customer = Counter()
        industry = Counter()
        for item in scenarios:
            if item.get("product_code"):
                product[str(item["product_code"])] += 1
            for value in values(item, "PRODUCT_MODEL", "PRODUCT", "SOURCE_PRODUCT"):
                product[value] += 1
            for value in values(item, "CUSTOMER_NAME", "CUSTOMER"):
                customer[value] += 1
            for value in values(item, "INDUSTRY"):
                industry[value] += 1
        return {
            "product": [{"label": k, "scenario_count": v} for k, v in product.most_common()],
            "customer": [{"label": k, "scenario_count": v} for k, v in customer.most_common()],
            "industry": [{"label": k, "scenario_count": v} for k, v in industry.most_common()],
            "scenario_count": len(scenarios),
            "evidence_count": sum(int(item.get("evidence_count") or 0) for item in scenarios),
        }


def create_legacy_scenario_read_router(
    db_path: str | Path,
    *,
    template_dir: str | Path | None = None,
) -> tuple[APIRouter, LegacyScenarioReadRepository]:
    repository = LegacyScenarioReadRepository(db_path)
    templates = Jinja2Templates(
        directory=str(template_dir or (Path(__file__).resolve().parent / "templates"))
    )
    router = APIRouter()

    def require_ready() -> None:
        if not repository.status()["ready"]:
            raise HTTPException(status_code=503, detail="LEGACY_SCENARIO_TABLE_NOT_FOUND")

    @router.get("/api/legacy-scenarios/status")
    def legacy_scenario_status() -> dict[str, Any]:
        return repository.status()

    @router.get("/quality-scenarios", response_class=HTMLResponse, include_in_schema=False)
    def legacy_quality_scenarios(
        request: Request,
        q: str = "",
        status: str = "",
        product_code: str = "",
    ) -> HTMLResponse:
        require_ready()
        items = repository.scenarios(q=q, status=status, product_code=product_code)
        return templates.TemplateResponse(
            request,
            "legacy_quality_scenarios_read.html",
            {
                "items": items,
                "q": q,
                "status": status,
                "product_code": product_code,
            },
        )

    @router.get("/quality-scenario-assets", response_class=HTMLResponse, include_in_schema=False)
    def legacy_scenario_assets(request: Request) -> HTMLResponse:
        require_ready()
        items = repository.scenarios()
        return templates.TemplateResponse(
            request,
            "legacy_scenario_assets_read.html",
            {
                "items": items,
                "portrait": repository.portrait(items),
            },
        )

    @router.get(
        "/quality-scenario-assets/portrait",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def legacy_scenario_portrait(request: Request) -> HTMLResponse:
        require_ready()
        items = repository.scenarios()
        return templates.TemplateResponse(
            request,
            "legacy_scenario_portrait_read.html",
            {
                "items": items,
                "portrait": repository.portrait(items),
            },
        )

    @router.get(
        "/quality-scenario-assets/{scenario_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def legacy_scenario_detail(request: Request, scenario_id: str) -> HTMLResponse:
        require_ready()
        item = repository.scenario(scenario_id)
        if item is None:
            raise HTTPException(status_code=404, detail="LEGACY_SCENARIO_NOT_FOUND")
        return templates.TemplateResponse(
            request,
            "legacy_scenario_detail_read.html",
            {"item": item},
        )

    return router, repository
