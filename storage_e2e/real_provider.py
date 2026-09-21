from __future__ import annotations

from pathlib import Path

from runtime.adapters import StorageFieldResult
from runtime.providers import OpenAICompatibleProviderAdapter


class OpenAICompatibleStorageRealProvider(OpenAICompatibleProviderAdapter):
    """Compatibility wrapper for older Storage E2E callers.

    Provider HTTP/SDK execution is owned by runtime.providers. Storage only
    supplies its prompt and output schema/shape. New code should let
    ConfiguredAgentRuntime.load_agent auto-bind the provider directly.
    """

    def __init__(self, prompt_path: str | Path, *, timeout_seconds: int = 120) -> None:
        super().__init__(
            system_prompt=Path(prompt_path).read_text(encoding="utf-8"),
            output_schema=StorageFieldResult,
            timeout_seconds=timeout_seconds,
            response_shape="json_array",
        )


__all__ = ["OpenAICompatibleStorageRealProvider"]
