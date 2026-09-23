from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any

MAPPING_STATUSES = {'DRAFT','ACTIVE','INACTIVE','INVALID'}
SOURCE_TYPES = {'WEB','CLI','YAML_MIGRATION','BOOTSTRAP','BACKUP_IMPORT'}
TARGET_DOMAINS = {'ISSUE_FACT','PRODUCT_CONTEXT','OCCURRENCE','ESCAPE','SOLUTION','VERIFICATION','RECURRENCE','PRODUCT_EXTENSION'}
VALIDATION_LEVELS = {'VALID','WARNING','ERROR'}

@dataclass
class MappingItem:
    mapping_id: str
    canonical_field: str
    source_headers: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    target_domain: str = 'PRODUCT_EXTENSION'
    target_field: str = ''
    required: bool = False
    enabled: bool = True
    description: str = ''
    display_order: int = 0

    def to_dict(self): return asdict(self)

@dataclass
class MappingConfiguration:
    config_id: str
    business_type: str
    version: int
    status: str = 'DRAFT'
    source_type: str = 'WEB'
    source_file: str | None = None
    source_hash: str | None = None
    created_by: str | None = None
    activated_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    mappings: list[MappingItem] = field(default_factory=list)

    def to_dict(self):
        d=asdict(self); return d
