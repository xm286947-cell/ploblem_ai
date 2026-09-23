from pathlib import Path

from builder.validators import validate_json, load_json


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schema" / "standard_case.schema.json"


def test_valid_standard_case():
    data = load_json(ROOT / "tests" / "samples" / "valid_standard_case.json")
    errors = validate_json(data, SCHEMA)
    assert errors == []


def test_invalid_standard_case():
    data = load_json(ROOT / "tests" / "samples" / "invalid_standard_case.json")
    errors = validate_json(data, SCHEMA)
    assert errors
    assert any("case_id" in item for item in errors)
    assert any("UNKNOWN_STATUS" in item for item in errors)
