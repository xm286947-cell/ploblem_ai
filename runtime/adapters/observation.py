"""Adapter contract for future device collectors.

This module only defines the shared boundary and named stubs.  It deliberately
does not execute hardware commands or introduce a provider implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from runtime.contracts import RuntimeObservation


class RuntimeObservationAdapter(ABC):
    @abstractmethod
    def collect(self, device_context: dict[str, Any], metric_request: dict[str, Any]) -> Any:
        raise NotImplementedError

    @abstractmethod
    def normalize(self, raw_output: Any) -> RuntimeObservation:
        raise NotImplementedError


class _NotImplementedObservationAdapter(RuntimeObservationAdapter):
    def collect(self, device_context: dict[str, Any], metric_request: dict[str, Any]) -> Any:
        raise NotImplementedError("RUNTIME_ADAPTER_COLLECTION_NOT_IMPLEMENTED")

    def normalize(self, raw_output: Any) -> RuntimeObservation:
        raise NotImplementedError("RUNTIME_ADAPTER_NORMALIZATION_NOT_IMPLEMENTED")


class NVMeSmartAdapter(_NotImplementedObservationAdapter):
    pass


class EmmcExtCsdAdapter(_NotImplementedObservationAdapter):
    pass


class MtdUbiAdapter(_NotImplementedObservationAdapter):
    pass
