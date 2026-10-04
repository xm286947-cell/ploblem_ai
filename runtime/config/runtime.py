from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from runtime.config.errors import ConfigValidationError
from runtime.config.loader import AgentConfigLoader
from runtime.config.models import ResolvedAgentConfig
from runtime.contracts import AgentRequest
from runtime.engine.runtime import (
    AgentHandler,
    FaultInjector,
    LightweightExecutionEngine,
)
from runtime.providers import OpenAICompatibleProviderAdapter
from runtime.reliability.errors import RuntimeExecutionException, RuntimeStepError
from runtime.contracts import ErrorCategory
from runtime.store import SqliteTaskStore


class ConfiguredAgentRuntime(LightweightExecutionEngine):
    """P0.3 Runtime with AGENT-CONFIG-001 file-backed agent registration."""

    def __init__(
        self,
        store: SqliteTaskStore,
        *,
        config_loader: AgentConfigLoader,
        fault_injector: FaultInjector | None = None,
    ):
        super().__init__(store, fault_injector=fault_injector)
        self.config_loader = config_loader
        self._resolved_agent_configs: dict[str, ResolvedAgentConfig] = {}
        self._business_handlers: dict[str, AgentHandler] = {}
        self._provider_handlers_by_config_hash: dict[str, AgentHandler] = {}

    @staticmethod
    def _snapshot_provider_config(
        context: dict[str, Any],
        fallback: ResolvedAgentConfig,
    ) -> dict[str, Any]:
        runtime_context = context.get("runtime", {})
        agent_definition = runtime_context.get("agent_definition") or {}
        metadata = agent_definition.get("metadata") or {}
        model_policy = runtime_context.get("model_policy") or {}
        capabilities = metadata.get("provider_capabilities")
        if not isinstance(capabilities, dict):
            capabilities = fallback.provider.metadata.get("capabilities", {})
        return {
            "type": agent_definition.get("provider") or fallback.provider.type,
            "model": agent_definition.get("model") or fallback.provider.model,
            "model_ref": metadata.get("model_ref") or fallback.provider.profile_ref,
            "profile_ref": metadata.get("provider_ref") or fallback.provider.profile_ref,
            "agent_config_source": metadata.get("agent_config_source") or fallback.source_path,
            "model_config_source": metadata.get("model_config_source"),
            "config_hash": metadata.get("agent_config_hash") or fallback.config_hash,
            "capabilities": capabilities if isinstance(capabilities, dict) else {},
            "capability_source": metadata.get(
                "provider_capability_source",
                "UNKNOWN",
            ),
            "mode": metadata.get("provider_mode", fallback.provider.mode),
            "auth": metadata.get("provider_auth", fallback.provider.auth),
            "base_url_env": metadata.get("base_url_env"),
            "api_key_env": metadata.get("api_key_env"),
            "base_url": metadata.get("provider_base_url"),
            "sdk_retry": 0,
            "max_tokens": model_policy.get("max_tokens"),
            "temperature": model_policy.get("temperature"),
        }

    @staticmethod
    def _redact_secret_values(value: Any, secrets: list[str]) -> Any:
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="python")
        if isinstance(value, dict):
            return {
                key: ConfiguredAgentRuntime._redact_secret_values(item, secrets)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                ConfiguredAgentRuntime._redact_secret_values(item, secrets)
                for item in value
            ]
        if isinstance(value, str):
            redacted = value
            for secret in secrets:
                if secret:
                    redacted = redacted.replace(secret, "[REDACTED]")
            return redacted
        return value

    def _wrap_handler(
        self,
        handler: AgentHandler,
        resolved: ResolvedAgentConfig,
    ) -> AgentHandler:
        def configured_handler(
            payload: Any,
            context: dict[str, Any],
        ) -> Any:
            runtime_context = dict(context.get("runtime", {}))
            config_hash = (
                (runtime_context.get("agent_definition") or {})
                .get("metadata", {})
                .get("agent_config_hash", resolved.config_hash)
            )
            provider_config = self._snapshot_provider_config(
                context,
                resolved,
            )
            secret_value = (
                self.config_loader.get_runtime_api_key(
                    config_hash,
                    api_key_env=provider_config.get("api_key_env"),
                )
                if provider_config.get("auth", "api_key") == "api_key"
                else None
            )
            runtime_context["provider_config"] = {
                **provider_config,
                "api_key": secret_value,
            }
            runtime_context["agent_config_hash"] = config_hash
            configured_context = {
                **context,
                "runtime": runtime_context,
            }
            secrets = [secret_value] if secret_value else []
            try:
                # Provider responses are persisted by the Runtime after the
                # handler returns. Scrub the success value at this boundary
                # so a misbehaving adapter cannot persist an injected secret
                # in AgentResult, ExecutionCommit, or checkpoint state.
                return self._redact_secret_values(
                    handler(payload, configured_context),
                    secrets,
                )
            except RuntimeExecutionException as exc:
                raise RuntimeStepError(
                    self._redact_secret_values(str(exc), secrets),
                    code=exc.code,
                    category=exc.category,
                    retryable=exc.retryable,
                    details=self._redact_secret_values(exc.details, secrets),
                ) from exc
            except Exception as exc:
                raise RuntimeStepError(
                    self._redact_secret_values(str(exc), secrets),
                    retryable=False,
                ) from exc

        return configured_handler


    def _build_runtime_provider_handler(
        self,
        resolved: ResolvedAgentConfig,
    ) -> AgentHandler | None:
        if resolved.provider.type != "openai_compatible":
            return None

        adapter = OpenAICompatibleProviderAdapter(
            system_prompt=self.config_loader.read_prompt_text(resolved),
            output_schema=self.config_loader.get_output_schema(resolved),
            timeout_seconds=resolved.execution_policy.timeout_seconds,
            response_shape=resolved.definition.metadata.get(
                "provider_response_shape"
            ),
        )
        self._provider_handlers_by_config_hash[resolved.config_hash] = adapter

        def dispatch(payload: Any, context: dict[str, Any]) -> Any:
            runtime_context = context.get("runtime", {})
            config_hash = str(
                runtime_context.get("agent_config_hash")
                or (
                    (runtime_context.get("agent_definition") or {})
                    .get("metadata", {})
                    .get("agent_config_hash")
                )
                or resolved.config_hash
            )
            handler = self._provider_handlers_by_config_hash.get(config_hash)
            if handler is None:
                raise RuntimeStepError(
                    "provider handler for execution snapshot is unavailable",
                    code="PROVIDER_HANDLER_SNAPSHOT_MISSING",
                    category=ErrorCategory.CONFIG,
                    retryable=False,
                    details={"config_hash": config_hash},
                )
            return handler(payload, context)

        return dispatch

    def register_agent_from_config(
        self,
        path: str | Path,
        handler: AgentHandler | None = None,
    ) -> ResolvedAgentConfig:
        resolved = self.config_loader.load(path)
        if not resolved.definition.enabled:
            raise ConfigValidationError(
                "disabled agent cannot be registered",
                details={"agent_id": resolved.definition.agent_id},
            )

        agent_id = resolved.definition.agent_id
        if handler is not None:
            self._business_handlers[agent_id] = handler
        business_handler = self._business_handlers.get(agent_id)

        if business_handler is None:
            runtime_provider_handler = self._build_runtime_provider_handler(resolved)
            if runtime_provider_handler is None:
                self.register_agent_definition(resolved.definition)
            else:
                self.register_agent(
                    agent_id,
                    self._wrap_handler(runtime_provider_handler, resolved),
                    resolved.definition,
                )
        else:
            self.register_agent(
                agent_id,
                self._wrap_handler(business_handler, resolved),
                resolved.definition,
            )

        self._resolved_agent_configs[agent_id] = resolved
        return resolved

    def load_agent(
        self,
        path: str | Path,
        handler: AgentHandler | None = None,
    ) -> ResolvedAgentConfig:
        return self.register_agent_from_config(path, handler)

    def get_agent_config(self, agent_id: str) -> ResolvedAgentConfig:
        try:
            return self._resolved_agent_configs[agent_id]
        except KeyError as exc:
            raise KeyError(f"configured agent not found: {agent_id}") from exc

    def invoke(self, request: AgentRequest):
        resolved = self._resolved_agent_configs.get(request.agent_id)
        if resolved is not None:
            updates: dict[str, Any] = {}
            if request.execution_policy is None:
                updates["execution_policy"] = resolved.execution_policy
            if request.output_schema is None:
                updates["output_schema"] = resolved.output_schema.ref
            if updates:
                request = request.model_copy(update=updates)
        return super().invoke(request)


__all__ = ["ConfiguredAgentRuntime"]
