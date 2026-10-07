from pathlib import Path

import pytest

from storage_life.knowledge_model import (
    StorageLifetimeKnowledgeModel,
    StorageLifetimeKnowledgeModelError,
)


PRODUCT_ROOT = Path(__file__).resolve().parents[1]


def test_storage_lifetime_model_reuses_existing_canonical_device_types():
    model = StorageLifetimeKnowledgeModel.from_product_root(PRODUCT_ROOT)

    assert model.canonical_device_types == (
        "NAND Flash",
        "NOR Flash",
        "eMMC",
        "SSD",
    )
    assert model.primary_focus == "NAND Flash"
    assert "Raw NAND" not in model.canonical_device_types
    assert model.canonicalize_device_type("Raw NAND") == "NAND Flash"
    assert model.canonicalize_device_type("SPI NAND") == "NAND Flash"


def test_nand_profile_is_derived_from_existing_spec_and_parameter_semantics():
    model = StorageLifetimeKnowledgeModel.from_product_root(PRODUCT_ROOT)
    profile = model.device_profile("NAND Flash")
    parameters = {
        item["canonical_name"]: item for item in profile["parameters"]
    }

    assert profile["primary_focus"] is True
    assert "pe_cycles" in parameters
    assert parameters["pe_cycles"]["role"] == "endurance"
    assert "CALCULATION_RULE" in parameters["pe_cycles"][
        "knowledge_requirements"
    ]
    assert "S3" in parameters["pe_cycles"]["scenario_consumers"]

    # Existing spec_templates contains this field, but parameter_knowledge has
    # not yet classified its engineering role.  Do not guess the role.
    assert parameters["bit_flip_threshold"]["role"] is None
    assert parameters["bit_flip_threshold"]["knowledge_requirements"] == [
        "PARAMETER_DEFINITION"
    ]
    assert (
        parameters["bit_flip_threshold"]["knowledge_gap"]
        == "PARAMETER_ROLE_UNCLASSIFIED"
    )


def test_formula_bindings_are_registry_backed_and_non_executable_from_ai():
    model = StorageLifetimeKnowledgeModel.from_product_root(PRODUCT_ROOT)
    profile = model.device_profile("NAND Flash")
    formula_ids = {item["formula_id"] for item in profile["formulas"]}

    assert "NAND_PE_MARGIN_V1" in formula_ids
    assert "NAND_ERASE_COUNT_MARGIN_V1" in formula_ids
    assert "NAND_WEAR_DISTRIBUTION_V1" in formula_ids

    policy = model.snapshot()["policy"]["formula_policy"]
    assert policy["execution_source"] == "FormulaRegistry"
    assert policy["allow_ai_executable_formula"] is False
    assert policy["allow_prose_constant_extraction_at_runtime"] is False
    assert policy["require_registered_formula_id"] is True


def test_snapshot_exposes_model_gaps_instead_of_silently_inventing_roles():
    model = StorageLifetimeKnowledgeModel.from_product_root(PRODUCT_ROOT)
    snapshot = model.snapshot()

    assert snapshot["schema_version"] == "storage-lifetime-knowledge/v1"
    assert any(
        gap["device_type"] == "NAND Flash"
        and gap["canonical_name"] == "bit_flip_threshold"
        and gap["gap"] == "PARAMETER_ROLE_UNCLASSIFIED"
        for gap in snapshot["knowledge_gaps"]
    )


def test_unknown_device_type_fails_closed():
    model = StorageLifetimeKnowledgeModel.from_product_root(PRODUCT_ROOT)

    with pytest.raises(
        StorageLifetimeKnowledgeModelError,
        match="DEVICE_TYPE_UNSUPPORTED",
    ):
        model.device_profile("Unknown Storage")
