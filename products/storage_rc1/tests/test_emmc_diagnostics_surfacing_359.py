from __future__ import annotations

from storage_life import ai, product_api


RUNTIME_KEYS = {
    "device_life_time_est_typ_a": ("EXT_CSD[268]", "DEVICE_LIFE_TIME_EST_TYP_A"),
    "device_life_time_est_typ_b": ("EXT_CSD[269]", "DEVICE_LIFE_TIME_EST_TYP_B"),
    "pre_eol_info": ("EXT_CSD[267]", "PRE_EOL_INFO"),
    "bkops_status": ("EXT_CSD[246]", "BKOPS_STATUS"),
}


def _isolate_formal_knowledge(monkeypatch):
    monkeypatch.setattr(
        product_api,
        "_formal_knowledge",
        lambda *_args, **_kwargs: {"status": "NO_MATCH", "results": []},
    )


def test_runtime_emmc_diagnostics_surface_canonical_ext_csd_fields(monkeypatch):
    monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "runtime")
    _isolate_formal_knowledge(monkeypatch)

    result = product_api.diagnostics(device_type="eMMC")
    rows = {item["canonical_name"]: item for item in result["items"]}

    assert set(RUNTIME_KEYS) <= rows.keys()
    for key, (register, field_name) in RUNTIME_KEYS.items():
        row = rows[key]
        assert row["data_source"] == "EXT_CSD"
        assert register in row["read_method"]
        assert field_name in row["read_method"]
        assert row["runtime_observation"] == {
            "status": "UNKNOWN",
            "code": "RUNTIME_OBSERVATION_UNAVAILABLE",
            "observed_at": None,
            "value": None,
            "source": None,
        }


def test_legacy_emmc_diagnostic_keys_remain_available(monkeypatch):
    monkeypatch.setenv("STORAGE_LIFE_EXECUTION_MODE", "legacy")
    _isolate_formal_knowledge(monkeypatch)

    expected_keys = {item["canonical_name"] for item in ai.expected_fields("eMMC")}
    result = product_api.diagnostics(device_type="eMMC")
    visible_keys = {item["canonical_name"] for item in result["items"]}
    rows = {item["canonical_name"]: item for item in result["items"]}

    assert {"life_time_a", "life_time_b", "pre_eol"} & expected_keys
    assert {"life_time_a", "life_time_b", "pre_eol"} <= visible_keys
    assert rows["life_time_a"]["read_method"] == "读取 DEVICE_LIFE_TIME_EST_TYP_A"
    assert rows["life_time_b"]["read_method"] == "读取 DEVICE_LIFE_TIME_EST_TYP_B"
    assert rows["pre_eol"]["read_method"] == "读取 PRE_EOL_INFO"
