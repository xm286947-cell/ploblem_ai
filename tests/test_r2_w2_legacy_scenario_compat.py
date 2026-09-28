import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality_knowledge.web.legacy_scenario_compat import (
    LegacyScenarioReadRepository,
    create_legacy_scenario_read_router,
)


def _legacy_db(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE quality_scenario(
                scenario_id TEXT PRIMARY KEY,
                scenario_code TEXT,
                name TEXT,
                product_code TEXT,
                lifecycle_code TEXT,
                activity_code TEXT,
                status TEXT,
                version_no INTEGER,
                updated_at TEXT
            );
            CREATE TABLE quality_scenario_scope(
                scenario_id TEXT,
                scope_type TEXT,
                scope_value TEXT
            );
            CREATE TABLE quality_scenario_evidence(
                scenario_id TEXT,
                knowledge_id TEXT,
                evidence_summary TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO quality_scenario VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "OLD-SC-1",
                "OLD-001",
                "在线监控流畅性",
                "PLC",
                "SOFTWARE_DEBUGGING",
                "ONLINE_MONITORING",
                "PUBLISHED",
                3,
                "2026-09-01 10:00:00",
            ),
        )
        connection.executemany(
            "INSERT INTO quality_scenario_scope VALUES(?,?,?)",
            (
                ("OLD-SC-1", "PRODUCT_MODEL", "AM600"),
                ("OLD-SC-1", "CUSTOMER_NAME", "客户甲"),
                ("OLD-SC-1", "INDUSTRY", "锂电"),
            ),
        )
        connection.execute(
            "INSERT INTO quality_scenario_evidence VALUES(?,?,?)",
            ("OLD-SC-1", "QK-1", "{}"),
        )
    return path


def test_legacy_scenario_adapter_restores_historical_read_routes_without_writes(tmp_path: Path):
    db = _legacy_db(tmp_path / "legacy.db")
    repository = LegacyScenarioReadRepository(db)
    assert repository.status()["ready"] is True
    assert repository.status()["mode"] == "READ_ONLY"

    app = FastAPI()
    app.state.overall_shell_enabled = False
    app.include_router(create_legacy_scenario_read_router(db)[0])
    client = TestClient(app)

    listing = client.get("/quality-scenarios")
    assert listing.status_code == 200
    assert "原有质量场景工作台" in listing.text
    assert "在线监控流畅性" in listing.text
    assert "READ_ONLY" in listing.text

    assets = client.get("/quality-scenario-assets")
    assert assets.status_code == 200
    assert "原质量场景资产与产品画像" in assets.text
    assert "AM600" in assets.text

    portrait = client.get("/quality-scenario-assets/portrait")
    assert portrait.status_code == 200
    for marker in ("客户甲", "锂电", "AM600", "NO_LEGACY_WRITE"):
        assert marker in portrait.text

    detail = client.get("/quality-scenario-assets/OLD-SC-1")
    assert detail.status_code == 200
    assert "OLD-001" in detail.text
    assert "客户甲" in detail.text

    # Historical mutation endpoints are intentionally absent from the R2
    # compatibility layer. The old database remains byte-semantically read only.
    assert client.post("/quality-scenario-assets/OLD-SC-1/context").status_code == 405
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM quality_scenario").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM quality_scenario_scope").fetchone()[0] == 3


def test_legacy_scenario_adapter_fails_closed_when_historical_table_is_absent(tmp_path: Path):
    db = tmp_path / "legacy-empty.db"
    sqlite3.connect(db).close()
    repository = LegacyScenarioReadRepository(db)
    assert repository.status()["ready"] is False
    assert repository.status()["code"] == "LEGACY_SCENARIO_TABLE_NOT_FOUND"

    app = FastAPI()
    app.state.overall_shell_enabled = False
    app.include_router(create_legacy_scenario_read_router(db)[0])
    client = TestClient(app)

    assert client.get("/quality-scenarios").status_code == 503
    assert client.get("/quality-scenario-assets").status_code == 503
    assert client.get("/quality-scenario-assets/portrait").status_code == 503
