from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from runtime.contracts import (
    AgentDefinition,
    ExecutionMode,
    ExecutionPolicy,
    FailurePolicy,
    RetryBudget,
    RetryPolicy,
)
from runtime.config.errors import (
    ConfigReferenceNotFoundError,
    ConfigValidationError,
    SecretEnvNotFoundError,
)
from runtime.providers.endpoint import ProviderEndpointError, ProviderEndpointResolver
from runtime.config.models import (
    AgentConfig,
    ModelProfileConfig,
    ModelProfilesConfig,
    ProviderConfig,
    ProviderProfilesConfig,
    ResolvedAgentConfig,
    ResolvedProviderConfig,
    ResolvedReference,
)


_FORBIDDEN_SECRET_KEYS = {
    "api_key",
    "apikey",
    "secret",
    "client_secret",
    "password",
    "passwd",
    "access_token",
    "refresh_token",
    "bearer_token",
    "credential",
    "credentials",
}


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_value(value: Any) -> str:
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    elif isinstance(value, type) and issubclass(value, BaseModel):
        payload = value.model_json_schema()
    elif hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
    elif callable(value):
        payload = {
            "module": getattr(value, "__module__", None),
            "qualname": getattr(value, "__qualname__", repr(value)),
        }
    else:
        payload = value
    return _hash_bytes(_stable_json(payload).encode("utf-8"))


def _find_forbidden_secret_key(value: Any, path: str = "$") -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            direct_provider_api_key = (
                normalized == "api_key"
                and (
                    (
                        str(value.get("mode", "")).lower() == "direct"
                        and "type" in value
                    )
                    or (
                        "model" in value
                        and ("base_url" in value or "base_url_env" in value)
                    )
                )
            )
            if (
                normalized in _FORBIDDEN_SECRET_KEYS
                or normalized.endswith("_secret")
            ) and not direct_provider_api_key:
                return f"{path}.{key}"
            found = _find_forbidden_secret_key(item, f"{path}.{key}")
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _find_forbidden_secret_key(item, f"{path}[{index}]")
            if found:
                return found
    return None


class AgentConfigLoader:
    """Load, validate and resolve immutable Runtime agent configuration.

    Provider credentials may come from environment references (production)
    or direct provider config (test/integration only). Secret values are never
    retained on ResolvedAgentConfig, AgentDefinition or ExecutionPolicy, so
    Runtime snapshots/logs cannot serialize them.
    """

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        model_profiles: Mapping[str, Any] | str | Path | None = None,
        provider_profiles: Mapping[str, Any] | str | Path | None = None,
        schemas: Mapping[str, Any] | None = None,
        content_strategies: Any = None,
        completeness_gates: Mapping[str, Any] | None = None,
        environ: Mapping[str, str] | None = None,
    ):
        self.root = Path(root).resolve() if root is not None else None
        self.model_profiles, self.active_model = self._load_model_profiles(
            model_profiles
        )
        self.provider_profiles = self._load_provider_profiles(provider_profiles)
        self.schemas = dict(schemas or {})
        self.content_strategies = content_strategies
        self.completeness_gates = dict(completeness_gates or {})
        self.environ = environ if environ is not None else os.environ
        self._runtime_api_keys: dict[str, str] = {}

    def _load_model_profiles(
        self,
        source: Mapping[str, Any] | str | Path | None,
    ) -> tuple[dict[str, ModelProfileConfig], str | None]:
        if source is None:
            return {}, None
        if isinstance(source, Mapping):
            raw = dict(source)
        else:
            path = Path(source)
            if not path.is_absolute() and self.root is not None:
                path = self.root / path
            raw = self._load_yaml(path)
        try:
            profiles = ModelProfilesConfig.model_validate(raw)
        except ValidationError as exc:
            raise ConfigValidationError(
                "invalid model profile configuration",
                details={"errors": exc.errors(include_input=False)},
            ) from exc
        return profiles.models, profiles.active_model

    def _load_provider_profiles(
        self,
        source: Mapping[str, Any] | str | Path | None,
    ) -> dict[str, ProviderConfig]:
        if source is None:
            return {}
        if isinstance(source, Mapping):
            raw = dict(source)
        else:
            path = Path(source)
            if not path.is_absolute() and self.root is not None:
                path = self.root / path
            raw = self._load_yaml(path)
        if "providers" not in raw:
            raw = {"providers": raw}
        try:
            profiles = ProviderProfilesConfig.model_validate(raw)
        except ValidationError as exc:
            raise ConfigValidationError(
                "invalid provider profile configuration",
                details={"errors": exc.errors(include_input=False)},
            ) from exc
        return profiles.providers

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigReferenceNotFoundError("config", str(path)) from exc
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ConfigValidationError(
                "invalid YAML",
                details={"path": str(path)},
            ) from exc
        if not isinstance(raw, dict):
            raise ConfigValidationError(
                "agent config root must be a mapping",
                details={"path": str(path)},
            )
        forbidden = _find_forbidden_secret_key(raw)
        if forbidden:
            raise ConfigValidationError(
                "literal secret fields are forbidden; use *_env references",
                details={"path": str(path), "field_path": forbidden},
            )
        return raw

    def _resolve_path(self, ref: str, config_path: Path) -> Path:
        candidate = Path(ref)
        if candidate.is_absolute():
            return candidate
        options = []
        if self.root is not None:
            options.append(self.root / candidate)
        options.append(config_path.parent / candidate)
        for option in options:
            if option.exists():
                return option.resolve()
        raise ConfigReferenceNotFoundError("prompt", ref)

    def _resolve_prompt(
        self,
        ref: str,
        version: str | None,
        config_path: Path,
    ) -> ResolvedReference:
        path = self._resolve_path(ref, config_path)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ConfigReferenceNotFoundError("prompt", ref) from exc
        return ResolvedReference(
            ref=ref,
            version=version,
            content_hash=_hash_bytes(data),
        )

    def _registry_value(self, kind: str, ref: str, registry: Any) -> Any:
        if registry is None:
            raise ConfigReferenceNotFoundError(kind, ref)
        if isinstance(registry, Mapping):
            if ref not in registry:
                raise ConfigReferenceNotFoundError(kind, ref)
            return registry[ref]
        getter = getattr(registry, "get", None)
        if getter is None:
            raise ConfigReferenceNotFoundError(kind, ref)
        try:
            return getter(ref)
        except Exception as exc:
            raise ConfigReferenceNotFoundError(kind, ref) from exc

    def _resolve_registry_reference(
        self,
        *,
        kind: str,
        ref: str,
        version: str | None,
        registry: Any,
    ) -> ResolvedReference:
        value = self._registry_value(kind, ref, registry)
        resolved_version = version
        if resolved_version is None:
            resolved_version = getattr(value, "version", None)
        if resolved_version is None and "@" in ref:
            resolved_version = ref.rsplit("@", 1)[1]
        return ResolvedReference(
            ref=ref,
            version=resolved_version,
            content_hash=_hash_value(value),
        )

    def _require_env(self, env_name: str | None) -> str | None:
        if not env_name:
            return None
        value = self.environ.get(env_name)
        if value is None or value == "":
            raise SecretEnvNotFoundError(env_name)
        return value


    def read_prompt_text(self, resolved: ResolvedAgentConfig) -> str:
        """Read the exact prompt content resolved for one agent config."""
        path = self._resolve_path(
            resolved.prompt.ref,
            Path(resolved.source_path),
        )
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ConfigReferenceNotFoundError("prompt", resolved.prompt.ref) from exc
        if _hash_bytes(data) != resolved.prompt.content_hash:
            raise ConfigValidationError(
                "prompt content changed after agent config resolution",
                details={
                    "ref": resolved.prompt.ref,
                    "expected_hash": resolved.prompt.content_hash,
                    "actual_hash": _hash_bytes(data),
                },
            )
        return data.decode("utf-8")

    def get_output_schema(self, resolved: ResolvedAgentConfig) -> Any:
        """Return the configured runtime schema object without serializing it."""
        return self._registry_value(
            "output_schema",
            resolved.output_schema.ref,
            self.schemas,
        )

    def get_runtime_api_key(
        self,
        config_hash: str,
        *,
        api_key_env: str | None = None,
    ) -> str | None:
        if api_key_env:
            return self._require_env(api_key_env)
        return self._runtime_api_keys.get(config_hash)

    @staticmethod
    def _validate_provider_base_url(
        provider_type: str,
        base_url: str | None,
    ) -> None:
        if provider_type != "openai_compatible":
            return
        try:
            ProviderEndpointResolver.validate_base_url(base_url)
        except ProviderEndpointError as exc:
            raise ConfigValidationError(
                "invalid openai_compatible provider base_url",
                details=exc.details,
            ) from exc

    def _resolve_provider(
        self,
        config: AgentConfig,
    ) -> tuple[
        ResolvedProviderConfig,
        str | None,
        ModelProfileConfig | None,
    ]:
        model_ref = config.model_ref or (
            self.active_model
            if not config.provider_ref and config.provider is None
            else None
        )
        if model_ref is not None:
            profile = self.model_profiles.get(model_ref)
            if profile is None:
                raise ConfigReferenceNotFoundError("model_profile", model_ref)

            mode = "env" if profile.base_url_env else "direct"
            auth = (
                "api_key"
                if profile.api_key is not None or profile.api_key_env is not None
                else "none"
            )
            base_url = (
                self._require_env(profile.base_url_env)
                if profile.base_url_env
                else profile.base_url
            )
            api_key = (
                self._require_env(profile.api_key_env)
                if profile.api_key_env
                else profile.api_key
            )
            self._validate_provider_base_url(profile.provider, base_url)

            return (
                ResolvedProviderConfig(
                    type=profile.provider,
                    mode=mode,
                    auth=auth,
                    profile_ref=model_ref,
                    model=profile.model,
                    base_url_env=profile.base_url_env,
                    api_key_env=profile.api_key_env,
                    base_url=base_url,
                    sdk_retry=0,
                    metadata={
                        **profile.metadata,
                        "model_ref": model_ref,
                    },
                ),
                api_key,
                profile,
            )

        # Legacy AGENT-CONFIG-001 provider path. Kept for compatibility only.
        if config.provider_ref:
            provider = self.provider_profiles.get(config.provider_ref)
            if provider is None:
                raise ConfigReferenceNotFoundError(
                    "provider_profile",
                    config.provider_ref,
                )
            profile_ref = config.provider_ref
        else:
            provider = config.provider
            profile_ref = None

        if provider is None:
            raise ConfigValidationError(
                "model_ref is required when no active_model is configured",
                details={"agent_id": config.agent_id},
            )

        model = config.model or provider.model
        if model is None or not str(model).strip():
            raise ConfigValidationError(
                "model must be configured on the agent or provider",
                details={"agent_id": config.agent_id},
            )

        if provider.mode == "env":
            base_url = self._require_env(provider.base_url_env)
            api_key = (
                self._require_env(provider.api_key_env)
                if provider.auth == "api_key"
                else None
            )
        else:
            base_url = provider.base_url
            api_key = provider.api_key if provider.auth == "api_key" else None
        self._validate_provider_base_url(provider.type, base_url)

        return (
            ResolvedProviderConfig(
                type=provider.type,
                mode=provider.mode,
                auth=provider.auth,
                profile_ref=profile_ref,
                model=str(model),
                base_url_env=provider.base_url_env,
                api_key_env=provider.api_key_env,
                base_url=base_url,
                sdk_retry=0,
                metadata=dict(provider.metadata),
            ),
            api_key,
            None,
        )

    @staticmethod
    def _build_execution_policy(
        config: AgentConfig,
        model_profile: ModelProfileConfig | None = None,
    ) -> ExecutionPolicy:
        retry = config.execution.retry
        budget = config.execution.budget
        model_policy = {
            "max_tokens": (
                config.execution.model.max_tokens
                if config.execution.model.max_tokens is not None
                else (model_profile.max_tokens if model_profile else None)
            ),
            "temperature": (
                config.execution.model.temperature
                if config.execution.model.temperature is not None
                else (model_profile.temperature if model_profile else None)
            ),
            "sdk_retry": 0,
            **config.execution.model.metadata,
        }
        model_policy = {
            key: value
            for key, value in model_policy.items()
            if value is not None
        }
        return ExecutionPolicy(
            mode=ExecutionMode.SINGLE,
            timeout_seconds=config.execution.timeout_seconds,
            transport_retry=RetryPolicy(
                max_attempts=retry.transport_attempts,
                backoff_seconds=retry.backoff_seconds,
            ),
            validation_retry=RetryPolicy(
                max_attempts=retry.validation_attempts,
                backoff_seconds=retry.backoff_seconds,
            ),
            step_retry=RetryPolicy(
                max_attempts=retry.step_attempts,
                backoff_seconds=retry.backoff_seconds,
            ),
            retry_budget=RetryBudget(
                max_provider_calls_per_step=budget.max_provider_calls_per_step,
                max_provider_calls_per_task=budget.max_provider_calls_per_task,
                max_step_attempts=retry.step_attempts,
                max_validation_cycles_per_step_attempt=retry.validation_attempts,
                max_transport_attempts_per_model_call=retry.transport_attempts,
                max_elapsed_seconds_per_step=budget.max_elapsed_seconds_per_step,
            ),
            long_content_policy={
                "enabled": config.long_content.enabled,
                "strategy_ref": config.long_content.strategy_ref,
            },
            failure_policy=FailurePolicy.STOP,
            model_policy=model_policy,
        )

    def load(self, path: str | Path) -> ResolvedAgentConfig:
        config_path = Path(path)
        if not config_path.is_absolute() and self.root is not None:
            config_path = self.root / config_path
        config_path = config_path.resolve()

        raw = self._load_yaml(config_path)
        try:
            config = AgentConfig.model_validate(raw)
        except ValidationError as exc:
            raise ConfigValidationError(
                "invalid agent configuration",
                details={
                    "path": str(config_path),
                    "errors": exc.errors(include_input=False),
                },
            ) from exc

        provider, runtime_api_key, model_profile = self._resolve_provider(config)
        prompt = self._resolve_prompt(
            config.prompt.ref,
            config.prompt.version,
            config_path,
        )
        input_schema = None
        if config.input_schema is not None:
            input_schema = self._resolve_registry_reference(
                kind="input_schema",
                ref=config.input_schema.ref,
                version=config.input_schema.version,
                registry=self.schemas,
            )
        output_schema = self._resolve_registry_reference(
            kind="output_schema",
            ref=config.output_schema.ref,
            version=config.output_schema.version,
            registry=self.schemas,
        )

        content_strategy = None
        if config.long_content.enabled:
            assert config.long_content.strategy_ref is not None
            content_strategy = self._resolve_registry_reference(
                kind="content_strategy",
                ref=config.long_content.strategy_ref,
                version=None,
                registry=self.content_strategies,
            )

        completeness_gate = None
        if config.completeness_gate is not None:
            completeness_gate = self._resolve_registry_reference(
                kind="completeness_gate",
                ref=config.completeness_gate.ref,
                version=config.completeness_gate.version,
                registry=self.completeness_gates,
            )

        execution_policy = self._build_execution_policy(config, model_profile)
        safe_provider = provider.model_dump(mode="json")
        reference_material = {
            "prompt": prompt.model_dump(mode="json"),
            "input_schema": (
                input_schema.model_dump(mode="json")
                if input_schema is not None
                else None
            ),
            "output_schema": output_schema.model_dump(mode="json"),
            "content_strategy": (
                content_strategy.model_dump(mode="json")
                if content_strategy is not None
                else None
            ),
            "completeness_gate": (
                completeness_gate.model_dump(mode="json")
                if completeness_gate is not None
                else None
            ),
        }
        safe_config = config.model_dump(mode="json")
        inline_provider = safe_config.get("provider")
        if isinstance(inline_provider, dict) and "api_key" in inline_provider:
            inline_provider["api_key"] = "[DIRECT_SECRET]"
        effective_material = {
            "config": safe_config,
            "provider": safe_provider,
            "execution_policy": execution_policy.model_dump(mode="json"),
            "references": reference_material,
        }
        config_hash = _hash_bytes(
            _stable_json(effective_material).encode("utf-8")
        )
        if runtime_api_key:
            self._runtime_api_keys[config_hash] = runtime_api_key

        metadata = {
            **config.metadata,
            "agent_config_version": config.version,
            "agent_config_hash": config_hash,
            "model_ref": provider.profile_ref,
            "provider_ref": provider.profile_ref,
            "provider_type": provider.type,
            "provider_mode": provider.mode,
            "provider_auth": provider.auth,
            "provider_base_url": provider.base_url,
            "base_url_env": provider.base_url_env,
            "api_key_env": provider.api_key_env,
            "prompt_version": prompt.version,
            "prompt_hash": prompt.content_hash,
            "input_schema_ref": input_schema.ref if input_schema else None,
            "input_schema_version": input_schema.version if input_schema else None,
            "input_schema_hash": input_schema.content_hash if input_schema else None,
            "output_schema_version": output_schema.version,
            "output_schema_hash": output_schema.content_hash,
            "content_strategy_version": (
                content_strategy.version if content_strategy else None
            ),
            "content_strategy_hash": (
                content_strategy.content_hash if content_strategy else None
            ),
            "completeness_gate_ref": (
                completeness_gate.ref if completeness_gate else None
            ),
            "completeness_gate_version": (
                completeness_gate.version if completeness_gate else None
            ),
            "completeness_gate_hash": (
                completeness_gate.content_hash if completeness_gate else None
            ),
        }
        metadata = {
            key: value
            for key, value in metadata.items()
            if value is not None
        }

        definition = AgentDefinition(
            agent_id=config.agent_id,
            label=config.label,
            enabled=config.enabled,
            provider=provider.type,
            model=provider.model,
            prompt_ref=prompt.ref,
            output_schema=output_schema.ref,
            metadata=metadata,
            version=config.version,
            definition_hash=config_hash,
            content_strategy_ref=(
                content_strategy.ref if content_strategy else None
            ),
        )

        return ResolvedAgentConfig(
            source_path=str(config_path),
            config_version=config.version,
            config_hash=config_hash,
            definition=definition,
            execution_policy=execution_policy,
            provider=provider,
            prompt=prompt,
            input_schema=input_schema,
            output_schema=output_schema,
            content_strategy=content_strategy,
            completeness_gate=completeness_gate,
        )


__all__ = ["AgentConfigLoader"]
