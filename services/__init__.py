"""Service package public exports.

Keep domain services lazy so importing a product-specific submodule such as
services.hardware_case_backend does not eagerly load unrelated product domains.
"""

from __future__ import annotations

from typing import Any


__all__ = [
    "AnalysisArtifacts",
    "KnowledgeArtifacts",
    "KnowledgeService",
    "CONTRACT_VERSION",
    "HistoricalCaseConsumerService",
    "HistoricalCaseContractError",
]


def __getattr__(name: str) -> Any:
    if name in {"AnalysisArtifacts", "KnowledgeArtifacts", "KnowledgeService"}:
        from .knowledge_service import (
            AnalysisArtifacts,
            KnowledgeArtifacts,
            KnowledgeService,
        )
        values = {
            "AnalysisArtifacts": AnalysisArtifacts,
            "KnowledgeArtifacts": KnowledgeArtifacts,
            "KnowledgeService": KnowledgeService,
        }
    elif name in {
        "CONTRACT_VERSION",
        "HistoricalCaseConsumerService",
        "HistoricalCaseContractError",
    }:
        from .historical_case_contract import (
            CONTRACT_VERSION,
            HistoricalCaseConsumerService,
            HistoricalCaseContractError,
        )
        values = {
            "CONTRACT_VERSION": CONTRACT_VERSION,
            "HistoricalCaseConsumerService": HistoricalCaseConsumerService,
            "HistoricalCaseContractError": HistoricalCaseContractError,
        }
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = values[name]
    globals()[name] = value
    return value
