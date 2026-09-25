from fastapi.testclient import TestClient

from storage_life import ai, core, coverage
from storage_life.app import app


def _state(field, state, layer="identity"):
    return {"field_key": field, "state": state, "layer": layer}


def test_review_gate_not_required_when_key_coverage_is_closed_and_identity_is_grounded():
    cov = {
        "states": [
            _state("manufacturer", coverage.FOUND),
            _state("covered_part_numbers", coverage.FOUND),
            _state("pe_cycles", coverage.FOUND, "critical"),
            _state("retention", coverage.NOT_SPECIFIED, "critical"),
        ],
        "critical_unresolved": [], "diagnostic_unresolved": [], "identity_unresolved": [],
    }
    gate = coverage.compute_review_gate(coverage=cov, facts=[], schema_valid=True)
    assert gate == {"required": False, "status": "not_required", "queue": []}


def test_review_gate_requires_missing_vendor_even_when_search_is_closed_not_specified():
    cov = {
        "states": [
            _state("manufacturer", coverage.NOT_SPECIFIED),
            _state("covered_part_numbers", coverage.FOUND),
        ],
        "critical_unresolved": [], "diagnostic_unresolved": [], "identity_unresolved": [],
    }
    gate = coverage.compute_review_gate(coverage=cov, facts=[], schema_valid=True)
    assert gate["required"] is True
    assert {x["type"] for x in gate["queue"]} == {"identity_missing"}
    assert gate["queue"][0]["field_key"] == "manufacturer"


def test_review_gate_requires_critical_unresolved_and_conflict():
    cov = {
        "states": [
            _state("manufacturer", coverage.FOUND),
            _state("covered_part_numbers", coverage.FOUND),
            _state("retention", coverage.UNRESOLVED, "critical"),
        ],
        "critical_unresolved": ["retention"], "diagnostic_unresolved": [], "identity_unresolved": [],
    }
    gate = coverage.compute_review_gate(
        coverage=cov,
        facts=[{"field_key": "status_register", "status": "conflict"}],
        schema_valid=True,
    )
    assert gate["required"] is True
    assert ("critical_unresolved", "retention") in {(x["type"], x.get("field_key")) for x in gate["queue"]}
    assert ("conflict", "status_register") in {(x["type"], x.get("field_key")) for x in gate["queue"]}


def _seed_device_and_run(tmp_path, monkeypatch, *, review_required):
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "DB", tmp_path / "review-next001.sqlite3")
    with core.connect() as con:
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)", ("s", "x.pdf", "h", str(tmp_path / "x.pdf"), "", "", 1, "now"))
        con.execute("INSERT INTO devices VALUES (?,?,?,?,?)", ("d", "Vendor", "Family", "SSD", "s"))
        con.execute("""INSERT INTO extraction_runs
          (id,device_id,source_id,extraction_mode,schema_valid,model_calls,unresolved_evidence_json,review_required,review_queue_json,created_at)
          VALUES (?,?,?,?,?,?,?,?,?,?)""",
          ("r", "d", "s", "agent_single_pass_generic", 1, 1, "[]", 1 if review_required else 0,
           '[{"type":"critical_unresolved","field_key":"tbw"}]' if review_required else "[]", "now"))


def test_final_review_post_returns_not_required_without_calling_model(tmp_path, monkeypatch):
    _seed_device_and_run(tmp_path, monkeypatch, review_required=False)
    called = {"n": 0}
    monkeypatch.setattr(ai, "final_review", lambda *args, **kwargs: called.__setitem__("n", called["n"] + 1))
    client = TestClient(app)
    response = client.post("/api/devices/d/final-review")
    assert response.status_code == 200
    assert response.json()["overall_status"] == "not_required"
    assert called["n"] == 0


def test_final_review_failure_is_persisted_as_failed_not_not_run(tmp_path, monkeypatch):
    _seed_device_and_run(tmp_path, monkeypatch, review_required=True)
    monkeypatch.setattr(ai, "final_review", lambda *args, **kwargs: (_ for _ in ()).throw(ai.AIResponseError("provider failed")))
    client = TestClient(app)
    response = client.post("/api/devices/d/final-review")
    assert response.status_code == 502
    status = client.get("/api/devices/d/final-review").json()
    assert status["overall_status"] == "failed"
    assert "provider failed" in status["summary"]
