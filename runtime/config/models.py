from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from runtime.contracts import AgentDefinition, ExecutionPolicy


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReferenceConfig(ConfigModel):
    ref: str
    version: str | None = None


class ProviderConfig(ConfigModel):
    type: str
    mode: Literal["env", "direct"] = "env"
    base_url_env: str | None = None
    api_key_env: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_credential_mode(self):
        if self.mode == "env":
            if self.api_key is not None or self.base_url is not None:
                raise ValueError(
                    "env provider mode cannot configure direct api_key/base_url"
                )
            if self.type == "openai_compatible" and not self.api_key_env:
                raise ValueError(
                    "openai_compatible env provider requires api_key_env"
                )
        else:
            if self.api_key_env is not None or self.base_url_env is not None:
                raise ValueError(
                    "direct provider mode cannot configure *_env references"
                )
            if self.type == "openai_compatible" and not self.api_key:
                raise ValueError(
                    "openai_compatible direct provider requires api_key"
                )
        return self


class ModelExecutionConfig(ConfigModel):
    max_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetryConfig(ConfigModel):
    transport_attempts: int = Field(default=1, ge=1)
    validation_attempts: int = Field(default=1, ge=1)
    step_attempts: int = Field(default=1, ge=1)
    backoff_seconds: float = Field(default=0.0, ge=0)


class BudgetConfig(ConfigModel):
    max_provider_calls_per_step: int = Field(default=1, ge=1)
    max_provider_calls_per_task: int | None = Field(default=None, ge=1)
    max_elapsed_seconds_per_step: int | None = Field(default=None, ge=1)


class ExecutionConfig(ConfigModel):
    timeout_seconds: int | None = Field(default=None, ge=1)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    model: ModelExecutionConfig = Field(default_factory=ModelExecutionConfig)

    @model_validator(mode="after")
    def validate_budget_can_cover_each_retry_dimension(self):
        configured_attempts = max(
            self.retry.transport_attempts,
            self.retry.validation_attempts,
            self.retry.step_attempts,
        )
        if self.budget.max_provider_calls_per_step < configured_attempts:
            raise ValueError(
                "max_provider_calls_per_step must be >= every configured retry attempt limit"
            )
        return self


class LongContentConfig(ConfigModel):
    enabled: bool = False
    strategy_ref: str | None = None

    @model_validator(mode="after")
    def require_strategy_when_enabled(self):
        if self.enabled and not self.strategy_ref:
            raise ValueError("long_content.strategy_ref is required when enabled=true")
        return self


class AgentConfig(ConfigModel):
    agent_id: str
    version: str = "v1"
    label: str | None = None
    enabled: bool = True

    provider_ref: str | None = None
    provider: ProviderConfig | None = None
    model: str | None = None

    prompt: ReferenceConfig
    input_schema: ReferenceConfig | None = None
    output_schema: ReferenceConfig

    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    long_content: LongContentConfig = Field(default_factory=LongContentConfig)
    completeness_gate: ReferenceConfig | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_provider_source(self):
        if bool(self.provider_ref) == bool(self.provider):
            raise ValueError("exactly one of provider_ref or provider must be configured")
        return self


class ProviderProfilesConfig(ConfigModel):
    providers: dict[str, ProviderConfig]


class ResolvedProviderConfig(ConfigModel):
    type: str
    mode: Literal["env", "direct"] = "env"
    profile_ref: str | None = None
    model: str
    base_url_env: str | None = None
    api_key_env: str | None = None
    base_url: str | None = None
    sdk_retry: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResolvedReference(ConfigModel):
    ref: str
    version: str | None = None
    content_hash: str


class ResolvedAgentConfig(ConfigModel):
    source_path: str
    config_version: str
    config_hash: str
    definition: AgentDefinition
    execution_policy: ExecutionPolicy
    provider: ResolvedProviderConfig
    prompt: ResolvedReference
    input_schema: ResolvedReference | None = None
    output_schema: ResolvedReference
    content_strategy: ResolvedReference | None = None
    completeness_gate: ResolvedReference | None = None


__all__ = [
    "AgentConfig",
    "BudgetConfig",
    "ExecutionConfig",
    "LongContentConfig",
    "ModelExecutionConfig",
    "ProviderConfig",
    "ProviderProfilesConfig",
    "ReferenceConfig",
    "ResolvedAgentConfig",
    "ResolvedProviderConfig",
    "ResolvedReference",
    "RetryConfig",
]
