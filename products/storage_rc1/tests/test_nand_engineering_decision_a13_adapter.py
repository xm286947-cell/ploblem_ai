from __future__ import annotations

from storage_life import product_api
from storage_life import knowledge_release
from storage_life.knowledge_release import KnowledgeReleaseConsumer
from storage_life.lifetime_engine import FormulaRegistry
from storage_life.nand_engineering_decision import _profile, build_nand_engineering_decision


def test_required_budget_is_registered_and_reproducible_in_existing_engine():
    spec = FormulaRegistry.describe("nand.required_pe_budget")
    assert spec.formula_id == "NAND_REQUIRED_PE_BUDGET_V1"

    profile = _profile("gd5", 
        {"target_service_life_years": 5, "operating_days_per_year": 365, "design_margin_ratio": 0.25},
        {"pe_cycles_per_day": 1},
    )
    assert profile["required_pe_cycles"] == 2281.25
    trace = profile["derivation_trace"]
    assert trace["formula_id"] == "NAND_REQUIRED_PE_BUDGET_V1"
    assert trace["replay_trace"]["formula_id"] == "NAND_REQUIRED_PE_BUDGET_V1"
    assert {row["name"] for row in profile["assumptions"]} == {
        "pe_cycles_per_day", "target_service_life_years", "operating_days_per_year", "design_margin_ratio",
    }


def test_required_budget_fails_closed_without_explicit_pe_stress():
    profile = _profile("gd5",
        {"target_service_life_years": 5},
        {"logical_write_bytes_per_day": 1024},
    )
    assert profile["required_pe_cycles"] is None
    assert "PE_CYCLES_PER_DAY_REQUIRED_FOR_NAND_PE_BUDGET" in profile["missing_information"]


def test_implicit_calendar_default_is_labeled_as_assumption():
    profile = _profile("gd5", {"target_service_life_years": 5, "design_margin_ratio": 0.25}, {"pe_cycles_per_day": 1})
    calendar = next(item for item in profile["assumptions"] if item["name"] == "operating_days_per_year")
    assert calendar["rationale"] == "CALENDAR_DEFAULT_365_DAYS_PER_YEAR_NOT_DEVICE_FACT"


def test_seven_roles_share_one_test_only_case_and_never_promote_unknown(monkeypatch):
    facts = [{
        "canonical_name": "pe_cycles",
        "parameter_name": "P/E Cycle",
        "value": "100000",
        "unit": "cycles",
        "condition": "With ECC",
        "scope": "GD5 datasheet",
        "evidence": [{"evidence_id": "EVD-PE"}],
    }]
    monkeypatch.setattr(product_api, "device_slots", lambda _: {
        "device": {"id": "gd5", "device_type": "NAND Flash", "vendor": "GigaDevice", "model": "GD5F1GQ5"},
        "device_facts": facts,
    })
    class ValidTestBinding:
        def validate_storage_binding(self):
            return {"knowledge_release_version": "TEST_ONLY_GD5_G2_20261009"}

    monkeypatch.setattr(KnowledgeReleaseConsumer, "current", classmethod(lambda cls: ValidTestBinding()))
    monkeypatch.setattr(product_api, "_formal_knowledge", lambda *args, **kwargs: {
        "status": "NO_MATCH", "code": "NO_MATCHING_PUBLISHED_KNOWLEDGE",
        "knowledge_release_version": None, "results": [], "evidence_refs": [],
    })

    result = build_nand_engineering_decision("gd5", {
        "case_id": "A13-SANDBOX-001",
        "mission_profile": {"target_service_life_years": 5, "operating_days_per_year": 365, "design_margin_ratio": 0.25},
        "workload_profile": {"pe_cycles_per_day": 1},
    })

    assert result["classification"] == "TEST_ONLY"
    assert result["overall_status"] == "PARTIAL_FAIL_CLOSED"
    assert result["shared_case"]["required_profile"]["required_pe_cycles"] == 2281.25
    assert result["shared_case"]["source_fact_evidence_refs"] == ["EVD-PE"]
    assert result["shared_case"]["device_decision"] == "INSUFFICIENT_EVIDENCE"
    assert set(result["roles"]) == {
        "system_engineering", "hardware_engineering", "software_engineering",
        "procurement", "test_validation", "change_management", "runtime_lifetime",
    }
    assert all(view.get("status") for view in result["roles"].values())
    assert result["roles"]["procurement"]["automatic_purchase_approval"] is False
    assert result["roles"]["runtime_lifetime"]["status"] == "UNKNOWN"
    assert result["rules"]["provider_call_performed"] is False
    assert result["rules"]["formal_publish_performed"] is False


def test_binding_failure_fails_closed_without_knowledge_query(monkeypatch):
    class InvalidBinding:
        def validate_storage_binding(self):
            raise ValueError("RELEASE_VERSION_MISMATCH")

    monkeypatch.setattr(KnowledgeReleaseConsumer, "current", classmethod(lambda cls: InvalidBinding()))
    monkeypatch.setattr(product_api, "device_slots", lambda _: {
        "device": {"id": "gd5", "device_type": "NAND Flash"}, "device_facts": [],
    })
    queries = []
    monkeypatch.setattr(product_api, "_formal_knowledge", lambda *args, **kwargs: queries.append(args))

    result = build_nand_engineering_decision("gd5", {})
    assert result["shared_case"]["formal_knowledge"]["status"] == "UNKNOWN"
    assert not queries
    assert result["overall_status"] == "PARTIAL_FAIL_CLOSED"


def test_release_version_mismatch_is_not_consumed(monkeypatch):
    class ValidTestBinding:
        def validate_storage_binding(self):
            return {"knowledge_release_version": "TEST_ONLY_PINNED"}

    monkeypatch.setattr(KnowledgeReleaseConsumer, "current", classmethod(lambda cls: ValidTestBinding()))
    monkeypatch.setattr(product_api, "device_slots", lambda _: {
        "device": {"id": "gd5", "device_type": "NAND Flash"}, "device_facts": [],
    })
    monkeypatch.setattr(product_api, "_formal_knowledge", lambda *args, **kwargs: {
        "status": "MATCHED", "code": None, "knowledge_release_version": "OTHER_RELEASE",
        "results": [{"object_id": "KO-TEST", "evidence_refs": ["EVD-TEST"]}],
        "evidence_refs": ["EVD-TEST"],
    })

    result = build_nand_engineering_decision("gd5", {})
    domain = result["shared_case"]["formal_knowledge"]["domains"][0]
    assert domain["status"] == "UNKNOWN"
    assert domain["code"] == "RELEASE_VERSION_BINDING_MISMATCH"
    assert result["shared_case"]["formal_knowledge"]["knowledge_ids"] == []


def test_formal_knowledge_without_row_evidence_is_not_consumed(monkeypatch):
    class ValidTestBinding:
        def validate_storage_binding(self):
            return {"knowledge_release_version": "TEST_ONLY_PINNED"}

    monkeypatch.setattr(KnowledgeReleaseConsumer, "current", classmethod(lambda cls: ValidTestBinding()))
    monkeypatch.setattr(product_api, "device_slots", lambda _: {
        "device": {"id": "gd5", "device_type": "NAND Flash"}, "device_facts": [],
    })
    monkeypatch.setattr(product_api, "_formal_knowledge", lambda canonical, *_args, **_kwargs: {
        "status": "MATCHED", "code": None, "knowledge_release_version": "TEST_ONLY_PINNED",
        "results": ([{"object_id": "KO-UNSUPPORTED", "content": "P/E cycles 100K"}] if canonical == "pe_cycles" else []),
        "evidence_refs": ["EVD-ONLY-TOP-LEVEL"],
    })

    result = build_nand_engineering_decision("gd5", {
        "mission_profile": {"target_service_life_years": 5},
        "workload_profile": {"pe_cycles_per_day": 1},
    })
    pe_domain = result["shared_case"]["formal_knowledge"]["domains"][0]
    assert pe_domain["status"] == "UNKNOWN"
    assert pe_domain["code"] == "FORMAL_EVIDENCE_REQUIRED"
    assert result["shared_case"]["formal_knowledge"]["knowledge_ids"] == []
    assert result["shared_case"]["engineering_screens"]["pe_endurance"]["status"] == "UNKNOWN"


def test_evidence_bound_endurance_and_workload_change_role_screen_not_device_qualification(monkeypatch):
    monkeypatch.setattr(product_api, "device_slots", lambda _: {
        "device": {"id": "gd5", "device_type": "NAND Flash", "vendor": "GigaDevice", "model": "GD5F1GQ5"},
        "device_facts": [],
    })

    class ValidTestBinding:
        def validate_storage_binding(self):
            return {"knowledge_release_version": "KP-STORAGE-RC1-VALIDATION-001"}

    monkeypatch.setattr(KnowledgeReleaseConsumer, "current", classmethod(lambda cls: ValidTestBinding()))

    def knowledge(canonical, *_args, **_kwargs):
        if canonical != "pe_cycles":
            return {"status": "NO_MATCH", "knowledge_release_version": "KP-STORAGE-RC1-VALIDATION-001", "results": [], "evidence_refs": []}
        row = {
            "object_id": "KO-TEST-PE",
            "title": "P/E endurance with ECC",
            "content": "P/E cycles with ECC: 100K",
            "conditions": ["With internal ECC enabled"],
            "evidence_refs": ["EVD-TEST-PE"],
            "source_refs": ["SRC-TEST-GD5"],
        }
        return {"status": "MATCHED", "knowledge_release_version": "KP-STORAGE-RC1-VALIDATION-001", "results": [row], "evidence_refs": ["EVD-TEST-PE"]}

    monkeypatch.setattr(product_api, "_formal_knowledge", knowledge)
    base = {
        "mission_profile": {"target_service_life_years": 5, "operating_days_per_year": 365, "design_margin_ratio": 0.25},
        "system_conditions": {"internal_ecc_enabled": True},
    }
    low = build_nand_engineering_decision("gd5", {**base, "workload_profile": {"pe_cycles_per_day": 0.5}})
    high = build_nand_engineering_decision("gd5", {**base, "workload_profile": {"pe_cycles_per_day": 50}})

    assert low["shared_case"]["engineering_screens"]["pe_endurance"]["status"] == "WITHIN_RATING_SCREEN"
    assert high["shared_case"]["engineering_screens"]["pe_endurance"]["status"] == "EXCEEDS_RATING_SCREEN"
    assert low["roles"]["procurement"]["endurance_screen"] == low["roles"]["hardware_engineering"]["pe_endurance_screen"]
    assert low["roles"]["procurement"]["endurance_screen"]["evidence_refs"] == ["EVD-TEST-PE"]
    assert low["roles"]["procurement"]["screening_result"] == "WITHIN_P_E_RATING_SCREEN_ONLY"
    assert high["roles"]["procurement"]["screening_result"] == "EXCEEDS_P_E_RATING_SCREEN"
    assert low["roles"]["procurement"]["decision"] == "UNKNOWN"
    assert low["shared_case"]["device_decision"] == high["shared_case"]["device_decision"] == "INSUFFICIENT_EVIDENCE"
    assert low["roles"]["procurement"]["decision"] == high["roles"]["procurement"]["decision"]
    assert low["shared_case"]["formal_knowledge"]["knowledge_ids"] == high["shared_case"]["formal_knowledge"]["knowledge_ids"]


def test_all_roles_consume_same_evidence_bound_domain_snapshot(monkeypatch):
    monkeypatch.setattr(product_api, "device_slots", lambda _: {
        "device": {"id": "gd5", "device_type": "NAND Flash", "vendor": "GigaDevice", "model": "GD5F1GQ5"},
        "device_facts": [],
    })

    class ValidTestBinding:
        def validate_storage_binding(self):
            return {"knowledge_release_version": "TEST_ONLY_GD5_RELEASE"}

    monkeypatch.setattr(KnowledgeReleaseConsumer, "current", classmethod(lambda cls: ValidTestBinding()))
    rows = {
        "pe_cycles": {
            "object_id": "KO-PE", "title": "P/E with ECC", "content": "P/E cycles with ECC: 100K",
            "conditions": ["With internal ECC enabled"], "evidence_refs": ["EVD-PE"], "source_refs": ["SRC-GD5"],
        },
        "data_retention": {
            "object_id": "KO-RET", "title": "Data retention", "content": "Data retention: 10 years",
            "conditions": ["specified storage condition"], "evidence_refs": ["EVD-RET"], "source_refs": ["SRC-GD5"],
        },
        "ecc_capability": {
            "object_id": "KO-ECC", "title": "ECC capability", "content": "ECC corrects up to 4 bits per 528 bytes",
            "conditions": ["internal ECC enabled"], "evidence_refs": ["EVD-ECC"], "source_refs": ["SRC-GD5"],
        },
        "runtime_bad_block": {
            "object_id": "KO-BB", "title": "Bad block handling", "content": "Runtime cumulative counter not declared",
            "conditions": ["no runtime counter specified"], "evidence_refs": ["EVD-BB"], "source_refs": ["SRC-GD5"],
        },
    }

    def knowledge(canonical, *_args, **_kwargs):
        row = rows.get(canonical)
        return {
            "status": "MATCHED" if row else "NO_MATCH",
            "code": None if row else "NO_MATCHING_PUBLISHED_KNOWLEDGE",
            "knowledge_release_version": "TEST_ONLY_GD5_RELEASE",
            "results": [row] if row else [],
            "evidence_refs": row["evidence_refs"] if row else [],
        }

    monkeypatch.setattr(product_api, "_formal_knowledge", knowledge)
    payload = {
        "case_id": "A13-SAME-CASE-TEST-ONLY",
        "mission_profile": {"target_service_life_years": 5, "required_retention_years": 10, "minimum_ecc_correctable_bits": 4},
        "workload_profile": {"pe_cycles_per_day": 0.5},
        "system_conditions": {"internal_ecc_enabled": True},
    }
    result = build_nand_engineering_decision("gd5", payload)

    expected_ids = {"KO-PE", "KO-RET", "KO-ECC", "KO-BB"}
    expected_evidence = {"EVD-PE", "EVD-RET", "EVD-ECC", "EVD-BB"}
    shared = result["shared_case"]["formal_knowledge"]
    assert shared["status"] == "READY"
    assert set(shared["knowledge_ids"]) == expected_ids
    assert set(shared["evidence_refs"]) == expected_evidence
    assert result["shared_case"]["device_decision"] == "INSUFFICIENT_EVIDENCE"

    # Every decision-facing role references the same release-bound knowledge snapshot.
    for role_name in ("system_engineering", "hardware_engineering", "software_engineering", "procurement", "test_validation", "change_management"):
        role = result["roles"][role_name]
        basis = role.get("knowledge_basis") or role.get("reassessment_baseline", {}).get("domain_basis")
        assert basis
        assert {item_id for item in basis.values() for item_id in item["knowledge_ids"]} <= expected_ids
        assert {ref for item in basis.values() for ref in item["evidence_refs"]} <= expected_evidence
    assert "未声明运行累计坏块计数器" in result["roles"]["test_validation"]["test_basis_by_domain"]["BAD_BLOCK"]
    assert result["roles"]["runtime_lifetime"]["status"] == "UNKNOWN"
    assert result["roles"]["runtime_lifetime"]["telemetry_status"] == "NOT_PROVIDED"
    assert result["roles"]["procurement"]["decision"] == "UNKNOWN"
    assert result["roles"]["procurement"]["screening_result"] == "WITHIN_P_E_RATING_SCREEN_ONLY"


def test_ecc_condition_and_runtime_inputs_never_create_unsupported_lifetime_claim(monkeypatch):
    monkeypatch.setattr(product_api, "device_slots", lambda _: {
        "device": {"id": "gd5", "device_type": "NAND Flash"}, "device_facts": [],
    })

    class ValidTestBinding:
        def validate_storage_binding(self):
            return {"knowledge_release_version": "KP-STORAGE-RC1-VALIDATION-001"}

    monkeypatch.setattr(KnowledgeReleaseConsumer, "current", classmethod(lambda cls: ValidTestBinding()))
    monkeypatch.setattr(product_api, "_formal_knowledge", lambda canonical, *_a, **_k: {
        "status": "MATCHED" if canonical == "pe_cycles" else "NO_MATCH",
        "knowledge_release_version": "KP-STORAGE-RC1-VALIDATION-001",
        "results": ([{"object_id": "KO-TEST-PE", "title": "P/E with ECC", "content": "P/E cycles with ECC: 100K", "conditions": ["With ECC"], "evidence_refs": ["EVD-TEST-PE"]}] if canonical == "pe_cycles" else []),
        "evidence_refs": (["EVD-TEST-PE"] if canonical == "pe_cycles" else []),
    })
    result = build_nand_engineering_decision("gd5", {
        "mission_profile": {"target_service_life_years": 5, "operating_days_per_year": 365},
        "workload_profile": {"pe_cycles_per_day": 1},
        "runtime_telemetry": {"classification": "TEST_ONLY", "erase_count": 12},
    })
    screen = result["shared_case"]["engineering_screens"]["pe_endurance"]
    assert screen["status"] == "UNKNOWN"
    assert screen["reason"] == "SOURCE_RATING_REQUIRES_ECC_APPLICABILITY_INPUT"
    runtime = result["roles"]["runtime_lifetime"]
    assert runtime["status"] == "UNKNOWN"
    assert runtime["telemetry_status"] == "TEST_INPUT_PROVIDED_NOT_DEVICE_OBSERVATION"
    assert runtime["telemetry"] == [{"classification": "TEST_ONLY", "erase_count": 12}]


def test_release_binding_resolves_inside_fresh_package(tmp_path, monkeypatch):
    package_root = tmp_path / "STORAGE_PRODUCT_MVP_RC1"
    module_file = package_root / "storage_life" / "knowledge_release.py"
    module_file.parent.mkdir(parents=True)
    binding = package_root / "contracts" / "release_binding" / "v1" / "release_binding.json"
    binding.parent.mkdir(parents=True)
    binding.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(knowledge_release, "__file__", str(module_file))

    assert knowledge_release._binding_path() == binding


def test_device_decision_page_exposes_controlled_test_inputs_and_test_only_label():
    from pathlib import Path

    page = Path(__file__).parents[1] / "storage_life" / "index.html"
    html = page.read_text(encoding="utf-8")
    assert "NAND 七类工程决策 · TEST_ONLY" in html
    assert "运行受控预验证" in html
    assert "不是器件规格或寿命结论" in html
    assert 'id="nandInternalEcc"' in html
    assert 'id="nandRetentionYears"' in html
    assert "查看本角色完整结构化数据" in html


def test_non_nand_device_is_rejected(monkeypatch):
    monkeypatch.setattr(product_api, "device_slots", lambda _: {
        "device": {"id": "ssd", "device_type": "SSD"}, "device_facts": [],
    })
    try:
        build_nand_engineering_decision("ssd", {})
    except ValueError as error:
        assert "NAND_ENGINEERING_DECISION_REQUIRES_NAND" in str(error)
    else:
        raise AssertionError("non-NAND device must be rejected")
