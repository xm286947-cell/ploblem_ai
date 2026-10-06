from __future__ import annotations

import json
from pathlib import Path

from storage_life.knowledge_release import KnowledgeReleaseConsumer


def _write_release(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "release_manifest.json").write_text(
        json.dumps(
            {
                "knowledge_release_version": "storage-semantic-test-v1",
                "contract_version": "knowledge-query/v1",
                "files": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "evidences.json").write_text(
        json.dumps({"evidences": []}),
        encoding="utf-8",
    )
    (root / "source_references.json").write_text(
        json.dumps({"source_references": []}),
        encoding="utf-8",
    )
    objects = [
        {
            "object_id": "KO-CALC",
            "status": "ACTIVE",
            "device_type": "NAND Flash",
            "title": "P/E margin rule",
            "summary": "Reviewed calculation knowledge",
            "content": "P/E endurance rule",
            "tags": [
                "storage-lifetime",
                "storage-parameter:pe_cycles",
                "storage-semantic:CALCULATION_RULE",
            ],
            "scope": ["storage_lifetime"],
            "evidence_refs": [],
            "source_refs": [],
            "metadata": {
                "storage_lifetime": {
                    "schema_version": "storage-lifetime-knowledge/v1",
                    "semantic_class": "CALCULATION_RULE",
                    "semantic_class_status": "REVIEWED",
                    "formal_consumable": True,
                    "canonical_parameters": ["pe_cycles"],
                    "scenario_consumers": ["S3"],
                }
            },
        },
        {
            "object_id": "KO-TEST",
            "status": "ACTIVE",
            "device_type": "NAND Flash",
            "title": "P/E validation",
            "summary": "Reviewed test rule",
            "content": "Validate endurance boundary",
            "tags": [
                "storage-lifetime",
                "storage-parameter:pe_cycles",
                "storage-semantic:TEST_RULE",
            ],
            "scope": ["storage_lifetime"],
            "evidence_refs": [],
            "source_refs": [],
            "metadata": {
                "storage_lifetime": {
                    "schema_version": "storage-lifetime-knowledge/v1",
                    "semantic_class": "TEST_RULE",
                    "semantic_class_status": "REVIEWED",
                    "formal_consumable": True,
                    "canonical_parameters": ["pe_cycles"],
                    "scenario_consumers": ["S4", "S5"],
                }
            },
        },
        {
            "object_id": "KO-LEGACY",
            "status": "ACTIVE",
            "device_type": "NAND Flash",
            "title": "Legacy P/E prose",
            "summary": "Old published text without reviewed Storage metadata",
            "content": "pe_cycles endurance",
            "tags": [],
            "scope": [],
            "evidence_refs": [],
            "source_refs": [],
            "metadata": {},
        },
    ]
    (root / "knowledge_objects.json").write_text(
        json.dumps({"objects": objects}),
        encoding="utf-8",
    )


def test_semantic_query_returns_only_reviewed_formal_storage_knowledge(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    _write_release(release)
    consumer = KnowledgeReleaseConsumer(release)

    result = consumer.query(
        "",
        device_type="NAND Flash",
        semantic_class="CALCULATION_RULE",
        canonical_parameter="pe_cycles",
        scenario_consumer="S3",
    )

    assert result["selection_mode"] == "REVIEWED_STORAGE_SEMANTIC"
    assert [item["object_id"] for item in result["results"]] == ["KO-CALC"]
    assert result["unknowns_or_gaps"] == []


def test_semantic_query_does_not_fall_back_to_legacy_text(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    _write_release(release)
    consumer = KnowledgeReleaseConsumer(release)

    result = consumer.query(
        "pe_cycles",
        device_type="NAND Flash",
        semantic_class="DIAGNOSTIC_RULE",
        canonical_parameter="pe_cycles",
        scenario_consumer="S4",
    )

    assert result["results"] == []
    assert result["unknowns_or_gaps"] == [
        "NO_MATCHING_REVIEWED_STORAGE_KNOWLEDGE"
    ]


def test_legacy_query_contract_remains_backward_compatible(
    tmp_path: Path,
) -> None:
    release = tmp_path / "release"
    _write_release(release)
    consumer = KnowledgeReleaseConsumer(release)

    result = consumer.query("pe_cycles", device_type="NAND Flash")

    assert result["selection_mode"] == "TEXT_AND_DEVICE"
    assert {item["object_id"] for item in result["results"]} == {
        "KO-CALC",
        "KO-TEST",
        "KO-LEGACY",
    }
