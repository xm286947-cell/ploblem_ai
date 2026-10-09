"""AI tagger boundary for Hardware Retrieval W1.

The model output is always untrusted.  This adapter exposes only Formal-derived
projection values to the model and delegates all acceptance decisions to
hardware_retrieval_metadata.build_retrieval_metadata().
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from services.hardware_retrieval_metadata import (
    ALLOWED_TAG_SOURCE_FIELDS,
    HardwareRetrievalMetadataError,
    RETRIEVAL_TAGGER_VERSION,
    build_retrieval_metadata,
    retrieval_source_values,
)

TAGGER_INPUT_CONTRACT_VERSION = "hardware-retrieval-tagger-input/v1"
TaggerInvoker = Callable[[dict[str, Any]], Mapping[str, Any]]


class HardwareRetrievalTaggerError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def build_tagger_input(projection: Mapping[str, Any]) -> dict[str, Any]:
    try:
        fields = {
            field: retrieval_source_values(projection, field)
            for field in ALLOWED_TAG_SOURCE_FIELDS
        }
    except HardwareRetrievalMetadataError as error:
        raise HardwareRetrievalTaggerError(error.code) from error

    return {
        "contract_version": TAGGER_INPUT_CONTRACT_VERSION,
        "tagger_version": RETRIEVAL_TAGGER_VERSION,
        "knowledge_id": str(projection.get("knowledge_id") or ""),
        "formal_object_hash": str(projection.get("formal_object_hash") or ""),
        "rules": {
            "FACT": "literal phrase already present in a referenced source field",
            "NORMALIZED": "mechanical normalization only; no semantic aliasing",
            "EXPANSION": "derived recall-only wording anchored to a literal source_term",
        },
        "fields": fields,
    }


class HardwareRetrievalTagger:
    """Provider-neutral W1 tagger; Runtime binding is a separate integration step."""

    def __init__(self, invoke: TaggerInvoker):
        if not callable(invoke):
            raise HardwareRetrievalTaggerError("TAGGER_INVOKER_REQUIRED")
        self.invoke = invoke

    def tag(self, projection: Mapping[str, Any]) -> dict[str, Any]:
        request = build_tagger_input(projection)
        try:
            response = self.invoke(request)
        except HardwareRetrievalTaggerError:
            raise
        except Exception as error:
            raise HardwareRetrievalTaggerError("TAGGER_INVOCATION_FAILED") from error

        if not isinstance(response, Mapping):
            raise HardwareRetrievalTaggerError("TAGGER_RESPONSE_INVALID")
        tags = response.get("tags")
        if not isinstance(tags, list):
            raise HardwareRetrievalTaggerError("TAGGER_RESPONSE_INVALID")
        try:
            return build_retrieval_metadata(projection, tags)
        except HardwareRetrievalMetadataError as error:
            raise HardwareRetrievalTaggerError(error.code) from error


__all__ = [
    "HardwareRetrievalTagger",
    "HardwareRetrievalTaggerError",
    "TAGGER_INPUT_CONTRACT_VERSION",
    "build_tagger_input",
]
