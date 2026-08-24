"""Deterministic PLC seed generation and catalog lifecycle validation."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

import yaml

from quality_knowledge.config_loader import normalize_header
from quality_knowledge.standard_fields.models import StandardField
from quality_knowledge.standard_fields.repository import StandardFieldCatalogError, StandardFieldRepository


PLC_DOMAINS = {
    "identity": "ISSUE_FACT",
    "issue_fact": "ISSUE_FACT",
    "product_context": "PRODUCT_CONTEXT",
    "occurrence": "OCCURRENCE",
    "escape": "ESCAPE",
    "solution": "SOLUTION",
    "verification": "VERIFICATION",
    "recurrence": "RECURRENCE",
    "extension": "PRODUCT_EXTENSION",
}


class PlcSeedValidationError(StandardFieldCatalogError):
    """Raised when the immutable PLC field seed cannot produce a valid catalog."""


class StandardFieldService:
    def __init__(
        self,
        repository: StandardFieldRepository,
        plc_seed_path: str | Path,
        *,
        technical_field_exclusions: dict[str, dict] | None = None,
    ):
        self.repository = repository
        self.plc_seed_path = Path(plc_seed_path)
        self.technical_field_exclusions = dict(technical_field_exclusions or {})

    def load_plc_seed(self) -> tuple[dict, list[StandardField], str]:
        if not self.plc_seed_path.exists():
            raise PlcSeedValidationError("PLC_SEED_NOT_FOUND")
        raw_bytes = self.plc_seed_path.read_bytes()
        source_hash = hashlib.sha256(raw_bytes).hexdigest()
        try:
            payload = yaml.safe_load(raw_bytes.decode("utf-8")) or {}
        except yaml.YAMLError as error:
            raise PlcSeedValidationError("PLC_SEED_PARSE_FAILED") from error
        if payload.get("business_type") != "PLC":
            raise PlcSeedValidationError("PLC_SEED_BUSINESS_TYPE_INVALID")

        fields: list[StandardField] = []
        order = 0
        for section, domain in PLC_DOMAINS.items():
            members = payload.get(section) or {}
            if not isinstance(members, dict):
                raise PlcSeedValidationError("PLC_SEED_SECTION_INVALID")
            for target_field, spec in members.items():
                if not isinstance(spec, dict):
                    raise PlcSeedValidationError("PLC_SEED_FIELD_INVALID")
                aliases = tuple(str(value).strip() for value in spec.get("aliases") or [] if str(value).strip())
                path = f"{domain}.{target_field}"
                is_technical = path in self.technical_field_exclusions
                label_zh = ""
                if not is_technical:
                    label_zh = next((value for value in aliases if re.search(r"[\u4e00-\u9fff]", value)), "")
                if not label_zh:
                    if not is_technical:
                        raise PlcSeedValidationError(f"PLC_SEED_LABEL_ZH_MISSING:{path}")
                    label_zh = aliases[0] if aliases else path
                fields.append(
                    StandardField(
                        target_domain=domain,
                        target_field=target_field,
                        canonical_field=target_field,
                        label_zh=label_zh,
                        description_zh=label_zh,
                        required=section == "identity" and target_field == "business_issue_id",
                        enabled=not is_technical,
                        aliases=aliases,
                        display_order=order,
                        is_technical=is_technical,
                    )
                )
                order += 1

        paths = [field.path for field in fields]
        if len(fields) != 60 or len(set(paths)) != 60:
            raise PlcSeedValidationError("PLC_SEED_FIELD_COUNT_INVALID")
        alias_count = sum(len(field.aliases) for field in fields)
        if alias_count != 111:
            raise PlcSeedValidationError("PLC_SEED_ALIAS_COUNT_INVALID")
        if sum(field.enabled for field in fields) != 59 or sum(field.is_technical for field in fields) != 1:
            raise PlcSeedValidationError("PLC_SEED_BUSINESS_FIELD_COUNT_INVALID")
        required = [field for field in fields if field.required and field.enabled]
        if [field.path for field in required] != ["ISSUE_FACT.business_issue_id"]:
            raise PlcSeedValidationError("PLC_SEED_REQUIRED_FIELD_INVALID")
        return payload, fields, source_hash

    def seed_plc_mapping_and_catalog(
        self,
        connection: sqlite3.Connection,
        *,
        expected_hash: str,
        actor: str = "SYSTEM_INITIALIZER",
    ) -> dict:
        _, fields, actual_hash = self.load_plc_seed()
        if actual_hash != expected_hash:
            raise PlcSeedValidationError("PLC_SEED_HASH_MISMATCH")

        mapping = self.repository.active_mapping("PLC", connection)
        catalog = self.repository.active_catalog(connection)
        if mapping or catalog:
            self._validate_existing_seed(connection, fields, actual_hash)
            return {
                "outcome": "ALREADY_APPLIED",
                "mapping_config_id": mapping["config_id"],
                "catalog_version_id": catalog["catalog_version_id"],
                "business_field_count": sum(field.enabled for field in fields),
                "technical_column_count": sum(field.is_technical for field in fields),
                "seed_alias_count": sum(len(field.aliases) for field in fields),
            }

        mapping_id = "MAP-PLC-V1"
        mapping_content_hash = self._mapping_content_hash(fields)
        connection.execute(
            """
            INSERT INTO mapping_config(
                config_id, business_type, version, status, source_type,
                source_artifact_path, source_hash, content_hash, created_by, activated_at
            ) VALUES (?, 'PLC', 1, 'ACTIVE', 'PLC_BASELINE_SEED', ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (mapping_id, str(self.plc_seed_path), actual_hash, mapping_content_hash, actor),
        )
        mapping_item_ids: dict[str, str] = {}
        for field in fields:
            item_id = f"MI-PLC-V1-{field.target_domain}-{field.target_field}"
            mapping_item_ids[field.path] = item_id
            connection.execute(
                """
                INSERT INTO mapping_item(
                    mapping_item_id, config_id, canonical_field, target_domain,
                    target_field, required, enabled, description_zh, display_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    mapping_id,
                    field.canonical_field,
                    field.target_domain,
                    field.target_field,
                    int(field.required and field.enabled),
                    int(field.enabled),
                    field.description_zh,
                    field.display_order,
                ),
            )
            for alias_order, alias in enumerate(field.aliases):
                connection.execute(
                    """
                    INSERT INTO mapping_alias(
                        mapping_alias_id, mapping_item_id, alias, normalized_alias, alias_type, display_order
                    ) VALUES (?, ?, ?, ?, 'SOURCE_HEADER', ?)
                    """,
                    (f"MA-{item_id}-{alias_order}", item_id, alias, normalize_header(alias), alias_order),
                )
        connection.execute(
            """INSERT INTO mapping_activation_audit(
                    activation_audit_id, config_id, action, actor, reason
                ) VALUES (?, ?, 'ACTIVATE', ?, 'PLC baseline seed')""",
            ("MAA-PLC-V1", mapping_id, actor),
        )

        catalog_id = "SFC-PLC-V1"
        catalog_content_hash = self._catalog_content_hash(fields)
        connection.execute(
            """
            INSERT INTO standard_field_catalog_version(
                catalog_version_id, version_no, status, source_mapping_config_id,
                source_mapping_version, source_artifact_sha256, content_hash,
                created_by, approved_by, activated_at
            ) VALUES (?, 1, 'ACTIVE', ?, 1, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (catalog_id, mapping_id, actual_hash, catalog_content_hash, actor, actor),
        )

        aliases_seen: dict[str, str] = {}
        ambiguous_aliases: set[str] = set()
        for field in fields:
            field_id = f"SFD-PLC-V1-{field.target_domain}-{field.target_field}"
            fingerprint = self._semantic_fingerprint(field.path, "string", "")
            if not field.enabled:
                exclusion = self.technical_field_exclusions[field.path]
                connection.execute(
                    """INSERT INTO mapping_validation_result(
                            validation_result_id, config_id, level, code, message, details_json
                        ) VALUES (?, ?, 'INFO', 'KNOWN_TECHNICAL_COLUMN', ?, ?)""",
                    (
                        f"MVR-TECHNICAL-{field.target_field}",
                        mapping_id,
                        f"PLC source header '{field.aliases[0]}' is a known technical column.",
                        json.dumps(
                            {
                                "source_alias": field.aliases[0],
                                "target": None,
                                "field_path": field.path,
                                "handling": exclusion.get("handling", "RAW_ONLY"),
                                "reason": exclusion.get("reason"),
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    ),
                )
                continue
            connection.execute(
                """
                INSERT INTO standard_field_definition(
                    field_definition_id, catalog_version_id, target_domain,
                    target_field, canonical_field, label_zh, description_zh,
                    definition_status, data_type, required, enabled, sensitivity,
                    semantic_fingerprint, origin, source_mapping_item_id, display_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'NEEDS_ENRICHMENT', 'string', ?, 1,
                          'INTERNAL', ?, 'PLC_BASELINE', ?, ?)
                """,
                (
                    field_id,
                    catalog_id,
                    field.target_domain,
                    field.target_field,
                    field.canonical_field,
                    field.label_zh,
                    field.description_zh,
                    int(field.required),
                    fingerprint,
                    mapping_item_ids[field.path],
                    field.display_order,
                ),
            )
            for alias_order, alias in enumerate(field.aliases):
                normalized = normalize_header(alias)
                existing_path = aliases_seen.get(normalized)
                if existing_path == field.path:
                    # Variants with maintenance hints normalize to the same
                    # search key.  Mapping retains the originals, catalog
                    # search needs only one deterministic representative.
                    continue
                if existing_path and existing_path != field.path:
                    ambiguous_aliases.add(normalized)
                    continue
                aliases_seen[normalized] = field.path
                connection.execute(
                    """
                    INSERT INTO standard_field_alias(
                        field_alias_id, catalog_version_id, field_definition_id,
                        alias, normalized_alias, alias_type
                    ) VALUES (?, ?, ?, ?, ?, 'PLC_SEED')
                    """,
                    (f"SFA-{field_id}-{alias_order}", catalog_id, field_id, alias, normalized),
                )
        # Preserve all 111 aliases in Mapping for import diagnostics, while the
        # active standard catalog stores only unambiguous search aliases.
        for alias in sorted(ambiguous_aliases):
            candidate_rows = connection.execute(
                """
                SELECT item.target_domain || '.' || item.target_field AS path, alias.alias
                  FROM mapping_alias AS alias
                  JOIN mapping_item AS item ON item.mapping_item_id = alias.mapping_item_id
                 WHERE item.config_id = ? AND alias.normalized_alias = ? AND item.enabled = 1
                 ORDER BY path
                """,
                (mapping_id, alias),
            ).fetchall()
            candidates = [row["path"] for row in candidate_rows]
            original_alias = candidate_rows[0]["alias"] if candidate_rows else alias
            connection.execute(
                """INSERT INTO mapping_validation_result(
                        validation_result_id, config_id, level, code, message, details_json
                    ) VALUES (?, ?, 'WARNING', 'AMBIGUOUS_SEED_ALIAS', ?, ?)""",
                (
                    f"MVR-PLC-{alias}",
                    mapping_id,
                    f"PLC source header '{original_alias}' requires Preview-level disambiguation.",
                    json.dumps(
                        {
                            "original_alias": original_alias,
                            "normalized_alias": alias,
                            "candidates": candidates,
                            "conflict_type": "AMBIGUOUS_SEED_ALIAS",
                            "handling_requirement": "REQUIRES_PREVIEW_DISAMBIGUATION",
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            )
        self._validate_existing_seed(connection, fields, actual_hash)
        return {
            "outcome": "APPLIED",
            "mapping_config_id": mapping_id,
            "catalog_version_id": catalog_id,
            "business_field_count": sum(field.enabled for field in fields),
            "technical_column_count": sum(field.is_technical for field in fields),
            "seed_alias_count": sum(len(field.aliases) for field in fields),
        }

    def validate_catalog_for_activation(self, catalog_version_id: str, connection: sqlite3.Connection) -> list[dict]:
        errors: list[dict] = []
        duplicate_aliases = connection.execute(
            """
            SELECT normalized_alias, COUNT(*) AS count
              FROM standard_field_alias
             WHERE catalog_version_id = ?
             GROUP BY normalized_alias HAVING count > 1
            """,
            (catalog_version_id,),
        ).fetchall()
        for row in duplicate_aliases:
            errors.append({"code": "STANDARD_ALIAS_CONFLICT", "alias": row["normalized_alias"]})
        return errors

    def resolve_source_header(
        self, source_header: str, connection: sqlite3.Connection, mapping_config_id: str | None = None
    ) -> dict:
        """Resolve a source header without silently selecting ambiguous targets."""

        normalized = normalize_header(source_header)
        rows = connection.execute(
            """
            SELECT DISTINCT item.target_domain, item.target_field, item.canonical_field,
                            definition.label_zh
              FROM mapping_alias AS alias
              JOIN mapping_item AS item ON item.mapping_item_id = alias.mapping_item_id
              JOIN mapping_config AS config ON config.config_id = item.config_id
              LEFT JOIN standard_field_catalog_version AS catalog ON catalog.status = 'ACTIVE'
              LEFT JOIN standard_field_definition AS definition
                ON definition.catalog_version_id = catalog.catalog_version_id
               AND definition.target_domain = item.target_domain
               AND definition.target_field = item.target_field
             WHERE ((? IS NOT NULL AND config.config_id = ?)
                    OR (? IS NULL AND config.business_type = 'PLC' AND config.status = 'ACTIVE'))
               AND item.enabled = 1
               AND alias.normalized_alias = ?
             ORDER BY item.target_domain, item.target_field
            """,
            (mapping_config_id, mapping_config_id, mapping_config_id, normalized),
        ).fetchall()
        candidates = [
            {
                "path": f"{row[0]}.{row[1]}",
                "target_domain": row[0],
                "target_field": row[1],
                "canonical_field": row[2],
                "label_zh": row[3],
            }
            for row in rows
        ]
        if not candidates:
            technical = connection.execute(
                """
                SELECT item.target_domain, item.target_field, item.required
                  FROM mapping_alias AS alias
                  JOIN mapping_item AS item ON item.mapping_item_id = alias.mapping_item_id
                  JOIN mapping_config AS config ON config.config_id = item.config_id
                 WHERE ((? IS NOT NULL AND config.config_id = ?)
                        OR (? IS NULL AND config.business_type = 'PLC' AND config.status = 'ACTIVE'))
                   AND item.enabled = 0
                   AND alias.normalized_alias = ?
                """,
                (mapping_config_id, mapping_config_id, mapping_config_id, normalized),
            ).fetchone()
            if technical:
                return {
                    "source_header": source_header,
                    "normalized_header": normalized,
                    "status": "IGNORED",
                    "target": None,
                    "required": False,
                    "candidates": [],
                    "can_confirm": True,
                    "handling_requirement": "RAW_ONLY",
                }
            status = "UNMATCHED"
        elif len(candidates) == 1:
            status = "MATCHED"
        else:
            status = "CONFLICT"
        return {
            "source_header": source_header,
            "normalized_header": normalized,
            "status": status,
            "target": candidates[0]["path"] if status == "MATCHED" else None,
            "required": candidates[0]["path"] == "ISSUE_FACT.business_issue_id" if status == "MATCHED" else False,
            "candidates": candidates,
            "can_confirm": status != "CONFLICT",
            "handling_requirement": "REQUIRES_PREVIEW_DISAMBIGUATION" if status == "CONFLICT" else None,
        }

    @staticmethod
    def _mapping_content_hash(fields: list[StandardField]) -> str:
        value = [(field.path, field.required, field.aliases) for field in fields]
        return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode("utf-8")).hexdigest()

    @staticmethod
    def _catalog_content_hash(fields: list[StandardField]) -> str:
        value = [(field.path, field.label_zh, field.required) for field in fields if field.enabled]
        return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode("utf-8")).hexdigest()

    @staticmethod
    def _semantic_fingerprint(definition: str, data_type: str, enum_semantics: str) -> str:
        payload = "|".join((definition.strip().lower(), data_type.strip().lower(), enum_semantics.strip().lower()))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _validate_existing_seed(self, connection: sqlite3.Connection, fields: list[StandardField], source_hash: str) -> None:
        mapping = self.repository.active_mapping("PLC", connection)
        catalog = self.repository.active_catalog(connection)
        if mapping is None or catalog is None:
            raise PlcSeedValidationError("PLC_ACTIVE_MAPPING_OR_CATALOG_MISSING")
        if mapping["source_hash"] != source_hash or catalog["source_artifact_sha256"] != source_hash:
            raise PlcSeedValidationError("PLC_ACTIVE_SEED_HASH_MISMATCH")
        labels = connection.execute(
            """SELECT label_zh FROM standard_field_definition
               WHERE catalog_version_id = ? AND enabled = 1""",
            (catalog["catalog_version_id"],),
        ).fetchall()
        if len(labels) != 59 or any(not re.search(r"[\u4e00-\u9fff]", row["label_zh"] or "") for row in labels):
            raise PlcSeedValidationError("PLC_ACTIVE_LABEL_ZH_INVALID")
        mapped = connection.execute(
            "SELECT target_domain, target_field, required FROM mapping_item WHERE config_id = ? AND enabled = 1",
            (mapping["config_id"],),
        ).fetchall()
        catalogued = connection.execute(
            """SELECT target_domain, target_field, required FROM standard_field_definition
               WHERE catalog_version_id = ? AND enabled = 1""",
            (catalog["catalog_version_id"],),
        ).fetchall()
        expected = {
            (field.target_domain, field.target_field, int(field.required))
            for field in fields
            if field.enabled
        }
        if {tuple(row) for row in mapped} != expected or {tuple(row) for row in catalogued} != expected:
            raise PlcSeedValidationError("PLC_MAPPING_CATALOG_FIELDSET_MISMATCH")
