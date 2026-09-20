from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from runtime.config.errors import ConfigValidationError
from runtime.config.loader import AgentConfigLoader
from runtime.config.models import ResolvedAgentConfig
from runtime.contracts import AgentRequest
from runtime.engine.runtime import (
    AgentHandler,
    FaultInjector,
    LightweightExecutionEngine,
)
from runtime.reliability.errors import RuntimeExecutionException, RuntimeStepError
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

    @staticmethod
    def _snapshot_provider_config(
        context: dict[str, Any],
        fallback: ResolvedAgentConfig,
    ) -> dict[str, Any]:
        runtime_context = context.get("runtime", {})
        agent_definition = runtime_context.get("agent_definition") or {}
        metadata = agent_definition.get("metadata") or {}
        model_policy = runtime_context.get("model_policy") or {}
        return {
            "type": agent_definition.get("provider") or fallback.provider.type,
            "model": agent_definition.get("model") or fallback.provider.model,
            "profile_ref": metadata.get("provider_ref"),
            "base_url_env": metadata.get("base_url_env"),
            "api_key_env": metadata.get("api_key_env"),
            "base_url": metadata.get("provider_base_url"),
            "sdk_retry": 0,
            "max_tokens": model_policy.get("max_tokens"),
            "temperature": model_policy.get("temperature"),
        }

    @staticmethod
    def _redact_secret_values(value: Any, secrets: list[str]) -> Any:
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
            runtime_context["provider_config"] = self._snapshot_provider_config(
                context,
                resolved,
            )
            runtime_context["agent_config_hash"] = (
                (runtime_context.get("agent_definition") or {})
                .get("metadata", {})
                .get("agent_config_hash", resolved.config_hash)
            )
            configured_context = {
                **context,
                "runtime": runtime_context,
            }
            env_name = runtime_context["provider_config"].get("api_key_env")
            secret_value = (
                self.config_loader.environ.get(env_name)
                if env_name
                else None
            )
            secrets = [secret_value] if secret_value else []
            try:
                return handler(payload, configured_context)
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
            self.register_agent_definition(resolved.definition)
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
