from __future__ import annotations

import hashlib
import json

import pytest

from storage_life import product_api
from storage_life import knowledge_release
from storage_life.knowledge_release import KnowledgeReleaseConsumer, KnowledgeReleaseError
from storage_life.nand_engineering_decision import build_nand_engineering_decision


VERSION = "KP-STORAGE-RC1-VALIDATION-001"
SOURCE_ID = "src_72f434b245f91cdfea9b7a8b"
REVISION_ID = "rev_99e7833a64c2bf9b85272218"
PDF_SHA256 = "99e7833a64c2bf9b85272218e66b911ca33fb3504e89af539104edf617cf6c1a"


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _release_payload() -> tuple[list[dict], list[dict], list[dict]]:
    claims = [
        ("PE_ENDURANCE", "pe_cycles", "P/E cycles with ECC: 100K", "With internal ECC enabled", 4),
        ("RETENTION", "data_retention", "Data retention: 10 Years", "Datasheet stated rating; use conditions not expanded", 4),
        ("ECC_BIT_FLIP", "ecc_capability", "Page layout is 2K + 128 Bytes; ECC mode affects data/spare interpretation. No numeric correction strength is asserted by this fixture.", "Q003 mode conflict remains documented; TEST_ONLY scoped interpretation", 10),
        ("BAD_BLOCK", "runtime_bad_block", "TEST_ONLY negative-evidence assessment: the full source snapshot does not declare a runtime cumulative bad-block counter. Static maximum bad-block figures are not runtime telemetry.", "Whole-document scope; p29 erratum remains unapproved; no runtime value inferred", 29),
    ]
    objects = []
    evidences = []
    source_ref = f"{SOURCE_ID}@{REVISION_ID}"
    for i, (domain, canonical, content, condition, page) in enumerate(claims, start=1):
        oid, eid = f"KO-TEST-GD5-{domain}", f"EVD-TEST-GD5-{domain}"
        objects.append({
            "business_source_id": SOURCE_ID,
            "business_source_type": "PDF",
            "business_source_version": REVISION_ID,
            "candidate_id": None,
            "candidate_source_type": "TEST_ONLY_FIXTURE",
            "conditions": [condition],
            "content": content,
            "contract_version": "knowledge-object/v1",
            "device_type": "NAND Flash",
            "evidence_refs": [eid],
            "knowledge_release_version": VERSION,
            "limitations": ["TEST_ONLY; not human-approved Formal Knowledge; not a device qualification"],
            "object_id": oid,
            "object_type": "FACT",
            "object_version": 1,
            "producer": "TEST_ONLY_FIXTURE_FROM_HASH_VERIFIED_SOURCE_AUDIT",
            "scope": ["GD5F1GQ5 family; exact orderable part scope not asserted"],
            "source_refs": [source_ref],
            "status": "ACTIVE",
            "storage_domain": domain,
            "tags": ["gd5f1gq5", "nand", canonical, domain.lower()],
            "title": f"TEST_ONLY {domain}: {canonical}",
        })
        evidence_metadata = {
            "evidence_status": "TEST_ONLY_FIXTURE_FROM_SHA_VERIFIED_SOURCE_AUDIT",
            "snapshot_sha256": PDF_SHA256,
            "source_revision": REVISION_ID,
            "scope": "WHOLE_DOCUMENT_NEGATIVE_EVIDENCE" if domain == "BAD_BLOCK" else "PAGE_LOCATOR",
        }
        evidences.append({
            "evidence_id": eid,
            "excerpt": content,
            "locator": {
                "type": "DOCUMENT_SCOPE" if domain == "BAD_BLOCK" else "PAGE",
                "value": (
                    {"content_hash": PDF_SHA256, "section": "whole-document negative-evidence audit", "source_anchor": "whole-document"}
                    if domain == "BAD_BLOCK" else
                    {"content_hash": PDF_SHA256, "page": page, "section": "TEST_ONLY source-audit locator", "source_anchor": f"page:{page}"}
                ),
            },
            "metadata": evidence_metadata,
            "source": {
                "content_hash": "281ba0d508d109a37270d42649f5d3cd530df10c6a53fd997dfeaaf20917671a",
                "fingerprint": f"sha256:{PDF_SHA256}",
                "revision": REVISION_ID,
                "source_id": SOURCE_ID,
                "source_type": "PDF",
                "uri": "local://verified-public-source-snapshot/STDL-PM-009_gd5f1gq5.pdf",
            },
        })
    sources = [{
        "content_hash": "281ba0d508d109a37270d42649f5d3cd530df10c6a53fd997dfeaaf20917671a",
        "publisher": "GigaDevice Semiconductor Inc.",
        "source_id": SOURCE_ID,
        "source_kind": "TEST_ONLY_PUBLIC_SOURCE_SNAPSHOT",
        "source_ref": source_ref,
        "source_type": "PDF",
        "source_version": REVISION_ID,
        "title": "STDL-PM-009_gd5f1gq5.pdf",
        "snapshot_sha256": PDF_SHA256,
    }]
    return objects, evidences, sources


def _write_release(root, *, tamper_content: bool = False) -> tuple[dict, dict]:
    root.mkdir(parents=True, exist_ok=True)
    objects, evidences, sources = _release_payload()
    if tamper_content:
        objects[0]["content"] += " TAMPERED"
    payloads = {
        "knowledge_objects.json": {"objects": objects},
        "evidences.json": {"evidences": evidences},
        "source_references.json": {"source_references": sources},
    }
    for name, value in payloads.items():
        (root / name).write_bytes(_canonical(value))
    snapshot_material = {
        "knowledge_release_version": VERSION,
        "objects": objects,
        "evidences": evidences,
        "source_references": sources,
    }
    snapshot_hash = hashlib.sha256(_canonical(snapshot_material)).hexdigest()
    entries = [
        {"path": name, "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest(), "size": (root / name).stat().st_size}
        for name in sorted(payloads)
    ]
    manifest = {
        "knowledge_release_version": VERSION,
        "contract_version": "knowledge-query/v1",
        "object_contract_version": "knowledge-object/v1",
        "created_at": "2026-10-10T00:00:00Z",
        "snapshot_hash": snapshot_hash,
        "object_count": len(objects),
        "evidence_count": len(evidences),
        "source_reference_count": len(sources),
        "files": entries,
        "migration_notes": "TEST_ONLY fixture release; no Formal Knowledge claim.",
        "compatibility_notes": "TEST_ONLY isolated consumer validation.",
    }
    (root / "release_manifest.json").write_bytes(_canonical(manifest))
    binding = {
        "binding_contract_version": "knowledge-release-binding/v1.0",
        "storage_product_version": "STORAGE_PRODUCT_MVP_RC1",
        "knowledge_release_version": VERSION,
        "knowledge_release_snapshot_hash": snapshot_hash,
        "knowledge_object_contract_version": "knowledge-object/v1",
        "knowledge_query_contract_version": "knowledge-query/v1",
        "common_evidence_contract_version": "common-evidence/v1.0",
        "storage_consumer_contract_version": "UKCI-01/V1.0",
        "release_class": "TEST_ONLY_FIXTURE",
        "qualification_state": "TEST_ONLY_NOT_FORMAL_APPROVAL",
        "compatibility_status": "PASS",
        "latest_floating_dependency": False,
        "direct_knowledge_db_access": False,
        "candidate_store_access": False,
        "storage_self_publish": False,
        "required_behavior": {
            "pinned_release_on_startup": True,
            "fail_closed_on_version_mismatch": True,
            "fail_closed_on_missing_release": True,
            "fail_closed_on_evidence_contract_mismatch": True,
            "upgrade_requires_new_binding": True,
            "rollback_requires_previous_binding": True,
        },
    }
    return manifest, binding


def _install_binding(path, binding):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(binding))


def _decision(workload: float, *, ecc: bool | None = True, retention: float = 8):
    payload = {
        "case_id": "TEST_ONLY:GD5:R5:SAME_MISSION",
        "mission_profile": {
            "target_service_life_years": 5,
            "operating_days_per_year": 365,
            "design_margin_ratio": 0.25,
            "required_retention_years": retention,
        },
        "workload_profile": {"pe_cycles_per_day": workload},
        "system_conditions": {} if ecc is None else {"internal_ecc_enabled": ecc},
    }
    return build_nand_engineering_decision("gd5-r5", payload)


def test_file_backed_test_release_binding_consumer_and_roles(monkeypatch, tmp_path):
    release_root = tmp_path / "release"
    manifest, binding = _write_release(release_root)
    binding_path = tmp_path / "contracts" / "release_binding.json"
    _install_binding(binding_path, binding)
    monkeypatch.setenv("STORAGE_KNOWLEDGE_RELEASE_DIR", str(release_root))
    monkeypatch.setattr(knowledge_release, "_binding_path", lambda: binding_path)
    monkeypatch.setattr(product_api, "device_slots", lambda _: {
        "device": {"id": "gd5-r5", "device_type": "NAND Flash", "vendor": "GigaDevice", "model": "GD5F1GQ5"},
        "device_facts": [],
    })

    # Same mission/workload first runs without any Release files, then with the
    # exact Test Release+binding. This exercises the real file consumer seam.
    monkeypatch.setenv("STORAGE_KNOWLEDGE_RELEASE_DIR", str(tmp_path / "missing-release"))
    absent = _decision(1)
    assert absent["shared_case"]["engineering_screens"]["pe_endurance"]["status"] == "UNKNOWN"
    assert absent["shared_case"]["formal_knowledge"]["domains"][0]["code"] == "KNOWLEDGE_RELEASE_NOT_FOUND"
    monkeypatch.setenv("STORAGE_KNOWLEDGE_RELEASE_DIR", str(release_root))

    consumer = KnowledgeReleaseConsumer.current()
    assert consumer.status()["snapshot_hash"] == manifest["snapshot_hash"]
    assert consumer.validate_storage_binding()["release_class"] == "TEST_ONLY_FIXTURE"
    query = consumer.query("GD5F1GQ5 P/E cycles with ECC 100K", device_type="NAND Flash")
    assert query["total"] >= 1
    assert consumer.evidence("EVD-TEST-GD5-PE_ENDURANCE")["evidence"]["metadata"]["evidence_status"].startswith("TEST_ONLY")

    result = _decision(1)
    assert result["classification"] == "TEST_ONLY"
    assert result["shared_case"]["formal_knowledge"]["status"] == "READY"
    assert result["shared_case"]["formal_knowledge"]["release_identity"]["classification"] == "TEST_ONLY_FIXTURE"
    assert {x["domain"] for x in result["shared_case"]["formal_knowledge"]["domains"] if x["status"] == "MATCHED"} == {
        "PE_ENDURANCE", "RETENTION", "ECC_BIT_FLIP", "BAD_BLOCK",
    }
    assert result["shared_case"]["engineering_screens"]["pe_endurance"]["status"] == "WITHIN_RATING_SCREEN"
    assert result["shared_case"]["engineering_screens"]["pe_endurance"]["status"] != absent["shared_case"]["engineering_screens"]["pe_endurance"]["status"]
    assert result["shared_case"]["engineering_screens"]["retention"]["status"] == "WITHIN_RATING_SCREEN"
    assert result["shared_case"]["engineering_screens"]["ecc_bit_flip"]["status"] == "CONDITION_APPLICABLE_LIMIT_NOT_ESTABLISHED"
    assert result["shared_case"]["engineering_screens"]["bad_block"]["status"] == "RUNTIME_COUNTER_NOT_DECLARED"
    assert result["shared_case"]["device_decision"] == "INSUFFICIENT_EVIDENCE"
    assert result["roles"]["runtime_lifetime"]["status"] == "UNKNOWN"
    assert result["roles"]["software_engineering"]["knowledge_basis"]["PE_ENDURANCE"]["evidence_refs"]

    ecc_off = _decision(1, ecc=False)
    overloaded = _decision(100)
    assert ecc_off["shared_case"]["engineering_screens"]["pe_endurance"]["status"] == "UNKNOWN"
    assert ecc_off["shared_case"]["engineering_screens"]["ecc_bit_flip"]["status"] == "CONDITION_MISMATCH"
    assert overloaded["shared_case"]["engineering_screens"]["pe_endurance"]["status"] == "EXCEEDS_RATING_SCREEN"
    assert overloaded["shared_case"]["device_decision"] == "INSUFFICIENT_EVIDENCE"


def test_release_snapshot_hash_is_verified_and_binding_hash_is_pinned(monkeypatch, tmp_path):
    release_root = tmp_path / "release"
    manifest, binding = _write_release(release_root)
    binding_path = tmp_path / "contracts" / "release_binding.json"
    _install_binding(binding_path, binding)
    monkeypatch.setenv("STORAGE_KNOWLEDGE_RELEASE_DIR", str(release_root))
    monkeypatch.setattr(knowledge_release, "_binding_path", lambda: binding_path)
    consumer = KnowledgeReleaseConsumer.current()
    assert consumer.validate_storage_binding()["knowledge_release_snapshot_hash"] == manifest["snapshot_hash"]

    binding["knowledge_release_snapshot_hash"] = "0" * 64
    _install_binding(binding_path, binding)
    with pytest.raises(KnowledgeReleaseError, match="RELEASE_SNAPSHOT_HASH_MISMATCH"):
        consumer.validate_storage_binding()


def test_release_payload_tamper_fails_closed_even_if_file_manifest_is_stale(tmp_path):
    release_root = tmp_path / "release"
    _write_release(release_root)
    path = release_root / "knowledge_objects.json"
    data = json.loads(path.read_text())
    data["objects"][0]["content"] += " changed"
    path.write_bytes(_canonical(data))
    status = KnowledgeReleaseConsumer(release_root).status()
    assert status["available"] is False
    assert status["code"] == "KNOWLEDGE_RELEASE_HASH_MISMATCH"
