from __future__ import annotations

import json
from pathlib import Path

from storage_life import product_api
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


class _FakeProductConsumer:
    def __init__(self, *, structured_results, legacy_results):
        self.structured_results = structured_results
        self.legacy_results = legacy_results
        self.calls = []

    def status(self):
        return {
            "available": True,
            "status": "READY",
            "knowledge_release_version": "release-v1",
        }

    def query(self, text, **kwargs):
        self.calls.append({"text": text, **kwargs})
        if kwargs.get("canonical_parameter"):
            return {
                "knowledge_release_version": "release-v1",
                "selection_mode": "REVIEWED_STORAGE_SEMANTIC",
                "results": list(self.structured_results),
            }
        return {
            "knowledge_release_version": "release-v1",
            "selection_mode": "TEXT_AND_DEVICE",
            "results": list(self.legacy_results),
        }


def test_product_formal_knowledge_prefers_reviewed_structured_match(
    monkeypatch,
) -> None:
    structured = [
        {
            "object_id": "KO-STRUCTURED",
            "title": "P/E reviewed rule",
            "evidence": [{"evidence_id": "EVD-STRUCTURED"}],
        }
    ]
    consumer = _FakeProductConsumer(
        structured_results=structured,
        legacy_results=[
            {
                "object_id": "KO-LEGACY",
                "evidence": [{"evidence_id": "EVD-LEGACY"}],
            }
        ],
    )
    monkeypatch.setattr(
        product_api.KnowledgeReleaseConsumer,
        "current",
        classmethod(lambda cls: consumer),
    )

    result = product_api._formal_knowledge(
        "pe_cycles",
        "P/E Cycle",
        "NAND Flash",
        context="comparison difference engineering meaning",
        scenario_consumer="S2",
    )

    assert result["status"] == "MATCHED"
    assert result["selection_mode"] == "REVIEWED_STORAGE_SEMANTIC"
    assert [item["object_id"] for item in result["results"]] == [
        "KO-STRUCTURED"
    ]
    assert result["evidence_refs"] == ["EVD-STRUCTURED"]
    assert len(consumer.calls) == 1
    assert consumer.calls[0]["canonical_parameter"] == "pe_cycles"
    assert consumer.calls[0]["scenario_consumer"] == "S2"


def test_product_formal_knowledge_falls_back_only_for_legacy_release(
    monkeypatch,
) -> None:
    consumer = _FakeProductConsumer(
        structured_results=[],
        legacy_results=[
            {
                "object_id": "KO-LEGACY",
                "title": "Legacy P/E knowledge",
                "evidence": [{"evidence_id": "EVD-LEGACY"}],
            }
        ],
    )
    monkeypatch.setattr(
        product_api.KnowledgeReleaseConsumer,
        "current",
        classmethod(lambda cls: consumer),
    )

    result = product_api._formal_knowledge(
        "pe_cycles",
        "P/E Cycle",
        "NAND Flash",
        context="comparison difference engineering meaning",
        scenario_consumer="S2",
    )

    assert result["status"] == "MATCHED"
    assert result["selection_mode"] == "TEXT_AND_DEVICE"
    assert [item["object_id"] for item in result["results"]] == ["KO-LEGACY"]
    assert len(consumer.calls) == 2
    assert consumer.calls[0]["canonical_parameter"] == "pe_cycles"
    assert "canonical_parameter" not in consumer.calls[1]
