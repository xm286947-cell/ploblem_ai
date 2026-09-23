"""Stable models for the P0 standard-field catalog."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StandardField:
    """A single field in the active cross-product field catalog."""

    target_domain: str
    target_field: str
    canonical_field: str
    label_zh: str
    description_zh: str
    required: bool
    enabled: bool
    aliases: tuple[str, ...]
    display_order: int
    is_technical: bool = False

    @property
    def path(self) -> str:
        return f"{self.target_domain}.{self.target_field}"


@dataclass(frozen=True)
class CatalogSearchResult:
    """Search result returned to mapping and intake clients."""

    field_definition_id: str
    catalog_version_id: str
    label_zh: str
    target_domain: str
    target_field: str
    canonical_field: str
    aliases: tuple[str, ...]
    required: bool

    @property
    def display_name(self) -> str:
        return f"{self.label_zh}｜{self.target_domain}.{self.target_field}"
