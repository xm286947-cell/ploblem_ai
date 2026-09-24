from __future__ import annotations

import json

import pytest

from test_assets.storage_rc1.mock_harness import (
    chat_completion,
    configure_fixture,
    load_all_fixtures,
    load_fixture,
    requests_for,
    running_server,
)


EXPECTED_IDS = [f"M{index:02d}" for index in range(1, 26)]
COVERAGE = {"FOUND", "NOT_FOUND", "NOT_APPLICABLE", "NOT_CHECKED", "AMBIGUOUS"}


def test_rc1_fixture_set_is_complete_and_unique() -> None:
    fixtures = load_all_fixtures()
    ids = [item["mock_id"] for item in fixtures]
    assert sorted(ids) == EXPECTED_IDS
    assert len(ids) == len(set(ids)) == 25
    for fixture in fixtures:
        assert fixture["purpose"]
        assert isinstance(fixture["request_match"], dict)
        assert isinstance(fixture["behavior"], dict)
        assert "payload" in fixture
        assert isinstance(fixture["expected"], dict)


def test_coverage_semantics_are_frozen() -> None:
    expected = {
        "M04": "NOT_FOUND",
        "M05": "NOT_APPLICABLE",
        "M06": "NOT_CHECKED",
        "M07": "AMBIGUOUS",
    }
    for mock_id, coverage in expected.items():
        fixture = load_fixture(mock_id)
        parameter = fixture["payload"]["parameters"][0]
        assert parameter["coverage"] == coverage
        assert parameter["coverage"] in COVERAGE

    assert load_fixture("M04")["expected"]["must_not_map_to"] == "NOT_CHECKED"
    assert load_fixture("M06")["expected"]["must_not_map_to"] == "NOT_FOUND"


def test_golden_parameter_fixture_has_three_product_categories() -> None:
    fixture = load_fixture("M03")
    categories = {item["category"] for item in fixture["payload"]["parameters"]}
    assert categories == {"KEY_SPEC", "KEY_DIAGNOSTIC", "COMPREHENSIVE"}

    must_exist = {
        "capacity",
        "emmc_spec_version",
        "operating_temperature",
        "endurance_usage_model",
        "enhanced_user_data_area",
        "reliable_write",
        "cache",
        "bkops",
        "device_life_time_estimation_a",
        "device_life_time_estimation_b",
        "pre_eol_information",
        "health_read_method",
    }
    ids = {item["parameter_id"] for item in fixture["payload"]["parameters"]}
    assert must_exist.issubset(ids)


def test_parameter_baseline_covers_all_four_rc1_device_families() -> None:
    expected = {
        "M03": "eMMC",
        "M23": "SSD/NVMe SSD",
        "M24": "Raw NAND",
        "M25": "NOR Flash",
    }
    for mock_id, family in expected.items():
        fixture = load_fixture(mock_id)
        categories = {item["category"] for item in fixture["payload"]["parameters"]}
        assert categories == {"KEY_SPEC", "KEY_DIAGNOSTIC", "COMPREHENSIVE"}
        if mock_id != "M03":
            assert fixture["expected"]["device_family"] == family
            assert fixture["expected"]["required_slots_must_remain_visible"] is True
        assert fixture["expected"]["auto_confirm"] is False


def test_ai_has_no_confirm_or_publish_authority() -> None:
    confirm = load_fixture("M18")
    publish = load_fixture("M19")

    assert confirm["payload"]["status"] == "CONFIRMED"
    assert confirm["expected"]["effective_review_status"] == "UNREVIEWED"
    assert confirm["expected"]["ignore_ai_review_authority"] is True

    assert publish["payload"]["status"] == "PUBLISHED"
    assert publish["expected"]["effective_publish_status"] == "CANDIDATE"
    assert publish["expected"]["human_publish_required"] is True


def test_diagnostic_fixture_never_fabricates_runtime_observation() -> None:
    fixture = load_fixture("M22")
    diagnostic = fixture["payload"]["diagnostic"]
    assert diagnostic["runtime_observation"] is None
    assert fixture["expected"]["runtime_value_state"] == "NOT_COLLECTED"
    assert fixture["expected"]["fabricate_runtime_value"] is False


@pytest.mark.parametrize("mock_id", ["M01", "M03", "M04", "M05", "M06", "M07", "M08", "M09", "M10", "M11", "M12", "M13", "M18", "M19", "M20", "M21", "M22", "M23", "M24", "M25"])
def test_fixture_is_served_by_shared_openai_mock(mock_id: str) -> None:
    fixture = load_fixture(mock_id)
    with running_server() as (host, port):
        configure_fixture(host, port, fixture)
        status, raw = chat_completion(host, port, mock_id)
        assert status == 200
        envelope = json.loads(raw.decode("utf-8"))
        content = envelope["choices"][0]["message"]["content"]
        if isinstance(fixture["payload"], (dict, list)):
            assert json.loads(content) == fixture["payload"]
        else:
            assert content == fixture["payload"]

        ledger = requests_for(host, port, mock_id)
        assert len(ledger) == 1
        assert ledger[0]["headers"]["Authorization"] == "[REDACTED]"


def test_provider_429_fixture_fails_once_then_recovers() -> None:
    fixture = load_fixture("M14")
    with running_server() as (host, port):
        configure_fixture(host, port, fixture)
        first_status, _ = chat_completion(host, port, "M14")
        second_status, second_raw = chat_completion(host, port, "M14")
        assert first_status == 429
        assert second_status == 200
        envelope = json.loads(second_raw.decode("utf-8"))
        assert json.loads(envelope["choices"][0]["message"]["content"]) == {"ok": True}


def test_timeout_fixture_is_longer_than_normal_test_budget() -> None:
    fixture = load_fixture("M15")
    assert fixture["behavior"]["delay_ms"] >= 2000
    assert fixture["expected"]["runtime"] == "TIMEOUT"
    assert fixture["expected"]["fabricate_fact"] is False


def test_truncation_fixture_returns_invalid_json_wire_body() -> None:
    fixture = load_fixture("M16")
    with running_server() as (host, port):
        configure_fixture(host, port, fixture)
        status, raw = chat_completion(host, port, "M16")
        assert status == 200
        with pytest.raises(json.JSONDecodeError):
            json.loads(raw.decode("utf-8"))


def test_semantic_repair_fixture_is_deliberately_unstructured() -> None:
    fixture = load_fixture("M17")
    assert isinstance(fixture["payload"], str)
    assert fixture["expected"]["runtime"] == "SEMANTIC_REPAIR_REQUIRED"
    assert fixture["expected"]["business_guess_forbidden"] is True
