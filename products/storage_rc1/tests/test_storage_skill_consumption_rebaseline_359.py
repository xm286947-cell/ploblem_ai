from storage_life import ai, parameter_baseline, templates


def test_359_ssd_direct_fact_contract_restores_frozen_fields():
    fields = {x["canonical_name"] for x in ai.expected_fields("SSD")}
    assert {
        "capacity",
        "nand_type",
        "interface",
        "protocol",
        "host_memory_buffer",
        "operating_temperature",
        "tbw",
        "plp",
    } <= fields


def test_359_timar_read_plan_targets_frozen_direct_fact_fields():
    pages = [
        (
            2,
            "KEY FEATURES\nCapacity 256GB/512GB/1TB/2TB\n"
            "PCIe Gen 4 interface with up to 4 lanes\n"
            "NVMe Revision 2.0\nSupporting host memory buffer\n"
            "TBW 768/1500/3000/6000 TB (WAF=1)\n"
            "Operating -25C to +85C\nPLP Optional",
            "text",
        ),
        (
            5,
            "PRODUCT LINE-UP\nTIMAR K97M8-Y 256GB SSD\n"
            "TIMAR K97M8-Y 1TB SSD",
            "text",
        ),
    ]
    plan = templates.build_read_plan(pages, "SSD", "TIMAR")
    targets = {field for row in plan for field in row.get("target_fields", [])}
    assert {
        "capacity",
        "nand_type",
        "interface",
        "protocol",
        "host_memory_buffer",
        "operating_temperature",
        "tbw",
        "plp",
    } <= targets


def test_359_ssd_product_baseline_keeps_hmb_and_interface_protocol_mapping():
    fields = parameter_baseline.product_fields("SSD", ai.expected_fields("SSD"))
    by_name = {x["canonical_name"]: x for x in fields}
    assert "host_memory_buffer" in by_name
    assert by_name["host_memory_buffer"]["requirement_level"] == "SHOULD"
    assert {"interface", "protocol"} <= set(by_name["interface_protocol"]["aliases"])


def test_359_nand_bad_block_observability_is_not_runtime_bad_block_alias():
    fields = parameter_baseline.product_fields("NAND Flash", ai.expected_fields("NAND Flash"))
    by_name = {x["canonical_name"]: x for x in fields}
    assert "bad_block_observability" in by_name
    assert "runtime_bad_block" not in by_name["bad_block_observability"]["aliases"]
    # The descriptive runtime-bad-block fact remains independently visible; it is not
    # promoted into an observability capability without an explicit acquisition mechanism.
    assert "runtime_bad_block" in by_name
