from __future__ import annotations

from storage_life import product_api


class _ProductKnowledgeConsumer:
    def __init__(self, *, reviewed_model: bool):
        self.reviewed_model = reviewed_model
        self.calls = []

    def status(self):
        return {
            "available": True,
            "status": "READY",
            "knowledge_release_version": "KP-SEMANTIC-001",
        }

    def query(
        self,
        text,
        *,
        device_type="",
        top_k=8,
        knowledge_release_version=None,
        semantic_class="",
        canonical_parameter="",
        scenario_consumer="",
    ):
        self.calls.append({
            "text": text,
            "device_type": device_type,
            "canonical_parameter": canonical_parameter,
            "scenario_consumer": scenario_consumer,
        })
        reviewed_other = {
            "object_id": "KO-OTHER",
            "status": "ACTIVE",
            "device_type": device_type,
            "title": "P/E calculation rule",
            "content": "Reviewed rule for pe_cycles",
            "tags": [
                "storage-parameter:pe_cycles",
                "storage-semantic:CALCULATION_RULE",
            ],
            "metadata": {
                "storage_lifetime": {
                    "semantic_class": "CALCULATION_RULE",
                    "semantic_class_status": "REVIEWED",
                    "formal_consumable": True,
                    "canonical_parameters": ["pe_cycles"],
                    "scenario_consumers": ["S3"],
                }
            },
            "evidence": [],
            "evidence_refs": ["EVD-OTHER"],
        }
        legacy = {
            "object_id": "KO-LEGACY",
            "status": "ACTIVE",
            "device_type": device_type,
            "title": "ECC status prose",
            "content": "ecc_status diagnostic guidance",
            "metadata": {},
            "evidence": [],
            "evidence_refs": ["EVD-LEGACY"],
        }

        if canonical_parameter or scenario_consumer:
            return {
                "knowledge_release_version": "KP-SEMANTIC-001",
                "selection_mode": "REVIEWED_STORAGE_SEMANTIC",
                "results": [],
            }
        if not text:
            return {
                "knowledge_release_version": "KP-SEMANTIC-001",
                "selection_mode": "TEXT_AND_DEVICE",
                "results": [reviewed_other] if self.reviewed_model else [],
            }
        return {
            "knowledge_release_version": "KP-SEMANTIC-001",
            "selection_mode": "TEXT_AND_DEVICE",
            "results": [legacy],
        }


def test_product_formal_knowledge_does_not_keyword_fallback_after_model_migration(
    monkeypatch,
):
    consumer = _ProductKnowledgeConsumer(reviewed_model=True)
    monkeypatch.setattr(
        product_api.KnowledgeReleaseConsumer,
        "current",
        classmethod(lambda cls: consumer),
    )

    result = product_api._formal_knowledge(
        "ecc_status",
        "ECC Status",
        "NAND Flash",
        context="diagnostic",
        scenario_consumer="S4",
    )

    assert result["status"] == "NO_MATCH"
    assert result["code"] == "NO_MATCHING_REVIEWED_STORAGE_KNOWLEDGE"
    assert result["selection_mode"] == "REVIEWED_STORAGE_SEMANTIC"
    assert consumer.calls[0]["text"] == ""
    assert not any(
        call["text"] and call["canonical_parameter"] == ""
        for call in consumer.calls
    )


def test_product_formal_knowledge_keeps_legacy_release_compatibility(
    monkeypatch,
):
    consumer = _ProductKnowledgeConsumer(reviewed_model=False)
    monkeypatch.setattr(
        product_api.KnowledgeReleaseConsumer,
        "current",
        classmethod(lambda cls: consumer),
    )

    result = product_api._formal_knowledge(
        "ecc_status",
        "ECC Status",
        "NAND Flash",
        context="diagnostic",
        scenario_consumer="S4",
    )

    assert result["status"] == "MATCHED"
    assert result["selection_mode"] == "LEGACY_FORMAL_COMPATIBILITY"
    assert [row["object_id"] for row in result["results"]] == [
        "KO-LEGACY"
    ]
    assert consumer.calls[0]["text"] == ""
    assert any(call["text"] for call in consumer.calls[1:])
