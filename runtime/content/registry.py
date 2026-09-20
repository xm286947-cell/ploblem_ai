from __future__ import annotations

from typing import Any, Callable, Protocol

from runtime.contracts import (
    AgentDefinition,
    ContentStrategyDefinition,
    SourceBundle,
)
from runtime.content.errors import (
    ContentProjectionRequiredError,
    ContentStrategyNotFoundError,
)


class ContentProjector(Protocol):
    def project(
        self,
        business_input: Any,
        context: dict[str, Any],
        definition: AgentDefinition | None = None,
    ) -> SourceBundle:
        ...


class CallableContentProjector:
    def __init__(
        self,
        fn: Callable[
            [Any, dict[str, Any], AgentDefinition | None],
            SourceBundle,
        ],
    ):
        self._fn = fn

    def project(
        self,
        business_input: Any,
        context: dict[str, Any],
        definition: AgentDefinition | None = None,
    ) -> SourceBundle:
        result = self._fn(business_input, context, definition)
        if not isinstance(result, SourceBundle):
            raise ContentProjectionRequiredError(
                "content projector must return SourceBundle",
                details={"returned_type": type(result).__name__},
            )
        return result


class SourceBundleIdentityProjector:
    """Explicit no-op projector.

    It accepts only an already projected SourceBundle. It deliberately refuses
    arbitrary dict/list input so Runtime never guesses business splitting rules.
    """

    def project(
        self,
        business_input: Any,
        context: dict[str, Any],
        definition: AgentDefinition | None = None,
    ) -> SourceBundle:
        if not isinstance(business_input, SourceBundle):
            raise ContentProjectionRequiredError(
                "arbitrary business input must be projected to SourceBundle first",
                details={"input_type": type(business_input).__name__},
            )
        return business_input


class ContentStrategyRegistry:
    def __init__(self):
        self._definitions: dict[str, ContentStrategyDefinition] = {}
        self._latest: dict[str, str] = {}
        self._projectors: dict[str, ContentProjector] = {}

    def register(self, definition: ContentStrategyDefinition) -> None:
        self._definitions[definition.ref] = definition
        self._latest[definition.strategy_id] = definition.ref

    def bind_projector(
        self,
        projector_ref: str,
        projector: ContentProjector,
    ) -> None:
        self._projectors[projector_ref] = projector

    def get(self, strategy_ref: str) -> ContentStrategyDefinition:
        resolved = (
            strategy_ref
            if "@" in strategy_ref
            else self._latest.get(strategy_ref, strategy_ref)
        )
        definition = self._definitions.get(resolved)
        if definition is None:
            raise ContentStrategyNotFoundError(
                f"content strategy not found: {strategy_ref}",
                details={"strategy_ref": strategy_ref},
            )
        return definition

    def project(
        self,
        strategy_ref: str,
        business_input: Any,
        context: dict[str, Any] | None = None,
        definition: AgentDefinition | None = None,
    ) -> SourceBundle:
        strategy = self.get(strategy_ref)
        projector = self._projectors.get(strategy.projector_ref)
        if projector is None:
            raise ContentStrategyNotFoundError(
                f"content projector not bound: {strategy.projector_ref}",
                details={
                    "strategy_ref": strategy.ref,
                    "projector_ref": strategy.projector_ref,
                },
            )
        return projector.project(
            business_input,
            context or {},
            definition,
        )
