"""Persistence for the clean P0 standard-field catalog.

This repository deliberately does not create or alter database objects.  The
P0 initializer owns schema creation and all seed activation.
"""

from __future__ import annotations

import json
import sqlite3
import hashlib
import uuid
from pathlib import Path
from typing import Iterable

from quality_knowledge.config_loader import normalize_header
from quality_knowledge.standard_fields.models import CatalogSearchResult, StandardField


class StandardFieldCatalogError(RuntimeError):
    """Raised when catalog lifecycle or integrity rules are violated."""


class StandardFieldRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def active_catalog(self, connection: sqlite3.Connection | None = None) -> dict | None:
        owns_connection = connection is None
        connection = connection or self.connect()
        try:
            row = connection.execute(
                "SELECT * FROM standard_field_catalog_version WHERE status = 'ACTIVE'"
            ).fetchone()
            return dict(row) if row else None
        finally:
            if owns_connection:
                connection.close()

    def active_mapping(self, business_type: str = "PLC", connection: sqlite3.Connection | None = None) -> dict | None:
        owns_connection = connection is None
        connection = connection or self.connect()
        try:
            row = connection.execute(
                "SELECT * FROM mapping_config WHERE business_type = ? AND status = 'ACTIVE'",
                (business_type,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            if owns_connection:
                connection.close()

    def search_active(self, query: str = "", limit: int = 100) -> list[CatalogSearchResult]:
        catalog = self.active_catalog()
        if catalog is None:
            raise StandardFieldCatalogError("STANDARD_FIELD_CATALOG_NOT_INITIALIZED")
        needle = normalize_header(query).lower()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT definition.*, GROUP_CONCAT(alias.alias, '\u001f') AS aliases
                FROM standard_field_definition AS definition
                LEFT JOIN standard_field_alias AS alias
                  ON alias.field_definition_id = definition.field_definition_id
                WHERE definition.catalog_version_id = ? AND definition.enabled = 1
                GROUP BY definition.field_definition_id
                ORDER BY definition.display_order
                """,
                (catalog["catalog_version_id"],),
            ).fetchall()
        results = []
        for row in rows:
            aliases = tuple(value for value in (row["aliases"] or "").split("\u001f") if value)
            searchable = [
                row["label_zh"],
                row["target_domain"],
                row["target_field"],
                row["canonical_field"],
                f"{row['target_domain']}.{row['target_field']}",
                *aliases,
            ]
            if needle and not any(needle in normalize_header(value).lower() for value in searchable):
                continue
            results.append(
                CatalogSearchResult(
                    field_definition_id=row["field_definition_id"],
                    catalog_version_id=row["catalog_version_id"],
                    label_zh=row["label_zh"],
                    target_domain=row["target_domain"],
                    target_field=row["target_field"],
                    canonical_field=row["canonical_field"],
                    aliases=aliases,
                    required=bool(row["required"]),
                )
            )
            if len(results) >= limit:
                break
        return results

    def create_product_starter_draft(self, product_code: str) -> dict:
        """Return catalog targets for a new product without copying old headers."""

        if not product_code or not product_code.strip():
            raise StandardFieldCatalogError("PRODUCT_CODE_REQUIRED")
        catalog = self.active_catalog()
        if catalog is None:
            raise StandardFieldCatalogError("STANDARD_FIELD_CATALOG_NOT_INITIALIZED")
        fields = self.search_active(limit=1000)
        return {
            "product_code": product_code.strip().upper(),
            "catalog_version_id": catalog["catalog_version_id"],
            "targets": [
                {
                    "display_name": field.display_name,
                    "target_domain": field.target_domain,
                    "target_field": field.target_field,
                    "canonical_field": field.canonical_field,
                    "required": field.required,
                    "source_headers": [],
                }
                for field in fields
            ],
        }

    def create_product(self, *, product_code: str, product_name: str, product_type: str, metadata: dict | None = None) -> dict:
        code = product_code.strip().upper()
        if not code or not product_name.strip() or not product_type.strip():
            raise StandardFieldCatalogError("PRODUCT_CONFIG_INCOMPLETE")
        if not code.replace("_", "").isalnum():
            raise StandardFieldCatalogError("PRODUCT_CODE_INVALID")
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM product_config WHERE product_code = ?", (code,)
            ).fetchone()
            if existing:
                if existing["product_name"] != product_name.strip() or existing["product_type"] != product_type.strip().upper():
                    raise StandardFieldCatalogError("PRODUCT_CODE_ALREADY_EXISTS")
                return {**dict(existing), "outcome": "ALREADY_EXISTS"}
            product_id = f"PRODUCT-{code}"
            connection.execute(
                """INSERT INTO product_config(
                       product_id, product_code, product_name, product_type, metadata_json
                   ) VALUES (?, ?, ?, ?, ?)""",
                (product_id, code, product_name.strip(), product_type.strip().upper(),
                 json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)),
            )
            connection.commit()
        return {
            "product_id": product_id, "product_code": code, "product_name": product_name.strip(),
            "product_type": product_type.strip().upper(), "enabled": True, "outcome": "CREATED",
        }

    def persist_product_starter_draft(self, product_code: str, created_by: str) -> dict:
        """Open a draft, cloning the active mapping when one already exists.

        A product's ACTIVE mapping is immutable.  Editing therefore always
        happens in the next DRAFT version, with source aliases and enabled
        states copied forward so the user does not have to rebuild a working
        mapping from scratch.
        """

        code = product_code.strip().upper()
        if not created_by.strip():
            raise StandardFieldCatalogError("MAPPING_ACTOR_REQUIRED")
        starter = self.create_product_starter_draft(code)
        with self.connect() as connection:
            product = connection.execute(
                "SELECT 1 FROM product_config WHERE product_code = ? AND enabled = 1", (code,)
            ).fetchone()
            if product is None:
                raise StandardFieldCatalogError("PRODUCT_NOT_FOUND")
            existing = connection.execute(
                "SELECT config_id FROM mapping_config WHERE business_type = ? AND status = 'DRAFT'",
                (code,),
            ).fetchone()
            if existing:
                # A controlled standard field may have been activated after this
                # product draft was created.  Keep the draft and its aliases, but
                # append any newly approved catalog targets so users do not have
                # to discard or rebuild their work.
                present = {
                    (row["target_domain"], row["target_field"])
                    for row in connection.execute(
                        "SELECT target_domain,target_field FROM mapping_item WHERE config_id = ?",
                        (existing["config_id"],),
                    ).fetchall()
                }
                next_order = connection.execute(
                    "SELECT COALESCE(MAX(display_order), -1) + 1 FROM mapping_item WHERE config_id = ?",
                    (existing["config_id"],),
                ).fetchone()[0]
                added = 0
                for target in starter["targets"]:
                    key = (target["target_domain"], target["target_field"])
                    if key in present:
                        continue
                    connection.execute(
                        """INSERT INTO mapping_item(
                               mapping_item_id, config_id, canonical_field, target_domain,
                               target_field, required, enabled, description_zh, display_order
                           ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                        (f"MI-{uuid.uuid4().hex}", existing["config_id"], target["canonical_field"],
                         target["target_domain"], target["target_field"], int(target["required"]),
                         target["display_name"], next_order + added),
                    )
                    added += 1
                connection.commit()
                return {
                    **self.get_mapping(existing["config_id"]),
                    "outcome": "SYNCHRONIZED" if added else "ALREADY_EXISTS",
                    "added_fields": added,
                }
            base = connection.execute(
                """SELECT config_id FROM mapping_config
                    WHERE business_type = ? AND status = 'ACTIVE'
                    ORDER BY version DESC LIMIT 1""",
                (code,),
            ).fetchone()
            version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM mapping_config WHERE business_type = ?", (code,)
            ).fetchone()[0]
            config_id = f"MAP-{code}-V{version}"
            content_hash = hashlib.sha256(json.dumps(
                {"targets": starter["targets"], "base_config_id": base["config_id"] if base else None},
                ensure_ascii=False, sort_keys=True,
            ).encode()).hexdigest()
            connection.execute(
                """INSERT INTO mapping_config(
                       config_id, business_type, version, status, source_type,
                       content_hash, created_by, metadata_json
                   ) VALUES (?, ?, ?, 'DRAFT', ?, ?, ?, ?)""",
                (config_id, code, version, "ACTIVE_CLONE" if base else "CATALOG_STARTER",
                 content_hash, created_by.strip(),
                 json.dumps({
                     "catalog_version_id": starter["catalog_version_id"],
                     "base_config_id": base["config_id"] if base else None,
                 }, sort_keys=True)),
            )
            for order, target in enumerate(starter["targets"]):
                base_item = connection.execute(
                    """SELECT mapping_item_id,enabled FROM mapping_item
                        WHERE config_id = ? AND target_domain = ? AND target_field = ?""",
                    (base["config_id"], target["target_domain"], target["target_field"]),
                ).fetchone() if base else None
                enabled = 1 if target["required"] else int(base_item["enabled"] if base_item else 1)
                mapping_item_id = f"MI-{uuid.uuid4().hex}"
                connection.execute(
                    """INSERT INTO mapping_item(
                           mapping_item_id, config_id, canonical_field, target_domain,
                           target_field, required, enabled, description_zh, display_order
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (mapping_item_id, config_id, target["canonical_field"],
                     target["target_domain"], target["target_field"], int(target["required"]),
                     enabled, target["display_name"], order),
                )
                if base_item:
                    for alias_order, alias in enumerate(connection.execute(
                        """SELECT alias,normalized_alias,alias_type FROM mapping_alias
                            WHERE mapping_item_id = ? ORDER BY display_order""",
                        (base_item["mapping_item_id"],),
                    )):
                        connection.execute(
                            """INSERT INTO mapping_alias(
                                   mapping_alias_id,mapping_item_id,alias,normalized_alias,alias_type,display_order
                               ) VALUES (?, ?, ?, ?, ?, ?)""",
                            (f"MA-{uuid.uuid4().hex}", mapping_item_id, alias["alias"],
                             alias["normalized_alias"], alias["alias_type"], alias_order),
                        )
            connection.commit()
        return {**self.get_mapping(config_id), "outcome": "CREATED"}

    def get_mapping(self, config_id: str) -> dict:
        with self.connect() as connection:
            config = connection.execute("SELECT * FROM mapping_config WHERE config_id = ?", (config_id,)).fetchone()
            if config is None:
                raise StandardFieldCatalogError("MAPPING_CONFIG_NOT_FOUND")
            items = []
            for row in connection.execute(
                "SELECT * FROM mapping_item WHERE config_id = ? ORDER BY display_order", (config_id,)
            ):
                item = dict(row)
                item["aliases"] = [alias[0] for alias in connection.execute(
                    "SELECT alias FROM mapping_alias WHERE mapping_item_id = ? ORDER BY display_order",
                    (row["mapping_item_id"],),
                )]
                items.append(item)
        return {**dict(config), "items": items}

    def list_mappings(self, business_type: str = "") -> list[dict]:
        query = "SELECT * FROM mapping_config"
        values: tuple[str, ...] = ()
        if business_type.strip():
            query += " WHERE business_type = ?"
            values = (business_type.strip().upper(),)
        query += " ORDER BY business_type, version DESC"
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(query, values)]

    def update_mapping_draft(self, config_id: str, *, bindings: Iterable[dict], updated_by: str) -> dict:
        if not updated_by.strip():
            raise StandardFieldCatalogError("MAPPING_ACTOR_REQUIRED")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            config = connection.execute("SELECT * FROM mapping_config WHERE config_id = ?", (config_id,)).fetchone()
            if config is None:
                raise StandardFieldCatalogError("MAPPING_CONFIG_NOT_FOUND")
            if config["status"] != "DRAFT":
                raise StandardFieldCatalogError("MAPPING_CONFIG_NOT_DRAFT")
            for binding in bindings:
                domain, field = str(binding.get("target_domain") or ""), str(binding.get("target_field") or "")
                item = connection.execute(
                    "SELECT mapping_item_id, required FROM mapping_item WHERE config_id = ? AND target_domain = ? AND target_field = ?",
                    (config_id, domain, field),
                ).fetchone()
                if item is None:
                    raise StandardFieldCatalogError("MAPPING_TARGET_NOT_IN_CATALOG")
                enabled = bool(binding.get("enabled", True))
                if item["required"] and not enabled:
                    raise StandardFieldCatalogError("MAPPING_REQUIRED_FIELD_DISABLED")
                connection.execute("UPDATE mapping_item SET enabled = ? WHERE mapping_item_id = ?", (int(enabled), item["mapping_item_id"]))
                connection.execute("DELETE FROM mapping_alias WHERE mapping_item_id = ?", (item["mapping_item_id"],))
                seen: set[str] = set()
                for order, alias in enumerate(binding.get("source_headers") or []):
                    alias = str(alias).strip()
                    normalized = normalize_header(alias)
                    if not alias or normalized in seen:
                        continue
                    seen.add(normalized)
                    connection.execute(
                        """INSERT INTO mapping_alias(
                               mapping_alias_id, mapping_item_id, alias, normalized_alias, alias_type, display_order
                           ) VALUES (?, ?, ?, ?, 'SOURCE_HEADER', ?)""",
                        (f"MA-{uuid.uuid4().hex}", item["mapping_item_id"], alias, normalized, order),
                    )
            rows = [dict(row) for row in connection.execute(
                """SELECT item.target_domain,item.target_field,item.enabled,alias.normalized_alias
                     FROM mapping_item item LEFT JOIN mapping_alias alias USING(mapping_item_id)
                    WHERE item.config_id = ? ORDER BY item.display_order,alias.display_order""", (config_id,)
            )]
            content_hash = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
            connection.execute("UPDATE mapping_config SET content_hash = ? WHERE config_id = ?", (content_hash, config_id))
            connection.execute("DELETE FROM mapping_validation_result WHERE config_id = ?", (config_id,))
            connection.commit()
        return self.get_mapping(config_id)

    def validate_mapping_draft(self, config_id: str) -> dict:
        with self.connect() as connection:
            config = connection.execute("SELECT status, metadata_json FROM mapping_config WHERE config_id = ?", (config_id,)).fetchone()
            if config is None:
                raise StandardFieldCatalogError("MAPPING_CONFIG_NOT_FOUND")
            if config["status"] != "DRAFT":
                raise StandardFieldCatalogError("MAPPING_CONFIG_NOT_DRAFT")
            connection.execute("DELETE FROM mapping_validation_result WHERE config_id = ?", (config_id,))
            required_without_header = connection.execute(
                """SELECT item.target_domain,item.target_field FROM mapping_item item
                    WHERE item.config_id = ? AND item.required = 1 AND item.enabled = 1
                      AND NOT EXISTS (SELECT 1 FROM mapping_alias alias WHERE alias.mapping_item_id = item.mapping_item_id)""",
                (config_id,),
            ).fetchall()
            alias_conflicts = connection.execute(
                """SELECT alias.normalized_alias, COUNT(DISTINCT item.mapping_item_id) AS target_count
                     FROM mapping_alias alias
                     JOIN mapping_item item ON item.mapping_item_id = alias.mapping_item_id
                    WHERE item.config_id = ? AND item.enabled = 1
                    GROUP BY alias.normalized_alias
                   HAVING COUNT(DISTINCT item.mapping_item_id) > 1""",
                (config_id,),
            ).fetchall()
            base_config_id = (json.loads(config["metadata_json"] or "{}").get("base_config_id"))
            blocking_alias_conflicts = []
            for row in alias_conflicts:
                current_targets = {
                    value[0] for value in connection.execute(
                        """SELECT item.target_domain || '.' || item.target_field
                             FROM mapping_alias alias JOIN mapping_item item USING(mapping_item_id)
                            WHERE item.config_id = ? AND item.enabled = 1 AND alias.normalized_alias = ?""",
                        (config_id, row["normalized_alias"]),
                    )
                }
                base_targets = {
                    value[0] for value in connection.execute(
                        """SELECT item.target_domain || '.' || item.target_field
                             FROM mapping_alias alias JOIN mapping_item item USING(mapping_item_id)
                            WHERE item.config_id = ? AND item.enabled = 1 AND alias.normalized_alias = ?""",
                        (base_config_id, row["normalized_alias"]),
                    )
                } if base_config_id else set()
                if current_targets != base_targets or len(base_targets) <= 1:
                    blocking_alias_conflicts.append(row)
            for row in required_without_header:
                connection.execute(
                    """INSERT INTO mapping_validation_result(
                           validation_result_id,config_id,level,code,message,details_json
                       ) VALUES (?,?,'ERROR','REQUIRED_SOURCE_HEADER_MISSING',?,?)""",
                    (f"MVR-{uuid.uuid4().hex}", config_id,
                     f"{row['target_domain']}.{row['target_field']} 缺少源表头",
                    json.dumps(dict(row), ensure_ascii=False)),
                )
            for row in blocking_alias_conflicts:
                connection.execute(
                    """INSERT INTO mapping_validation_result(
                           validation_result_id,config_id,level,code,message,details_json
                       ) VALUES (?,?,'ERROR','SOURCE_HEADER_TARGET_CONFLICT',?,?)""",
                    (f"MVR-{uuid.uuid4().hex}", config_id,
                     f"源表头 {row['normalized_alias']} 同时映射到多个目标字段",
                     json.dumps(dict(row), ensure_ascii=False)),
                )
            connection.commit()
        errors = (["REQUIRED_SOURCE_HEADER_MISSING" for _ in required_without_header]
                  + ["SOURCE_HEADER_TARGET_CONFLICT" for _ in blocking_alias_conflicts])
        return {"config_id": config_id, "valid": not errors, "errors": errors}

    def activate_mapping(self, config_id: str, actor: str) -> dict:
        if not actor.strip():
            raise StandardFieldCatalogError("MAPPING_ACTOR_REQUIRED")
        validation = self.validate_mapping_draft(config_id)
        if not validation["valid"]:
            raise StandardFieldCatalogError("MAPPING_CONFIG_INVALID")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            config = connection.execute("SELECT business_type FROM mapping_config WHERE config_id = ?", (config_id,)).fetchone()
            connection.execute("UPDATE mapping_config SET status = 'RETIRED' WHERE business_type = ? AND status = 'ACTIVE'", (config["business_type"],))
            connection.execute("UPDATE mapping_config SET status = 'ACTIVE', activated_at = CURRENT_TIMESTAMP WHERE config_id = ?", (config_id,))
            connection.execute(
                """INSERT INTO mapping_activation_audit(
                       activation_audit_id,config_id,action,actor,reason
                   ) VALUES (?,?,'ACTIVATE',?,'P0 catalog-backed mapping')""",
                (f"MAA-{uuid.uuid4().hex}", config_id, actor.strip()),
            )
            connection.commit()
        return {**self.get_mapping(config_id), "outcome": "ACTIVATED"}

    def create_controlled_draft(
        self,
        *,
        proposed_domain: str,
        proposed_field: str,
        label_zh: str,
        definition_zh: str,
        data_type: str,
        business_example: str,
        reuse_rationale: str,
        candidate_fields: Iterable[str],
        source_context: dict,
        requested_by: str,
        connection: sqlite3.Connection,
    ) -> dict:
        """Create the next catalog draft and record a controlled field request."""

        active = self.active_catalog(connection)
        if active is None:
            raise StandardFieldCatalogError("STANDARD_FIELD_CATALOG_NOT_INITIALIZED")
        if not proposed_field.isidentifier() or proposed_field.lower() != proposed_field:
            raise StandardFieldCatalogError("STANDARD_FIELD_KEY_INVALID")
        if not proposed_domain or not label_zh or not definition_zh:
            raise StandardFieldCatalogError("STANDARD_FIELD_REQUEST_INCOMPLETE")
        existing = connection.execute(
            """SELECT 1 FROM standard_field_definition
               WHERE catalog_version_id = ? AND target_domain = ? AND target_field = ?""",
            (active["catalog_version_id"], proposed_domain, proposed_field),
        ).fetchone()
        if existing:
            raise StandardFieldCatalogError("STANDARD_FIELD_ALREADY_EXISTS")
        next_version = connection.execute(
            "SELECT COALESCE(MAX(version_no), 0) + 1 FROM standard_field_catalog_version"
        ).fetchone()[0]
        catalog_id = f"SFC-DRAFT-{next_version}"
        connection.execute(
            """
            INSERT INTO standard_field_catalog_version(
                catalog_version_id, version_no, status, base_version_id,
                source_mapping_config_id, source_mapping_version,
                source_artifact_sha256, content_hash, created_by
            ) VALUES (?, ?, 'DRAFT', ?, ?, ?, ?, ?, ?)
            """,
            (
                catalog_id,
                next_version,
                active["catalog_version_id"],
                active["source_mapping_config_id"],
                active["source_mapping_version"],
                active["source_artifact_sha256"],
                active["content_hash"],
                requested_by,
            ),
        )
        connection.execute(
            """
            INSERT INTO standard_field_definition(
                field_definition_id, catalog_version_id, target_domain,
                target_field, canonical_field, label_zh, description_zh,
                definition_status, data_type, required, enabled, sensitivity,
                semantic_fingerprint, origin, source_mapping_item_id, display_order
            )
            SELECT 'SFD-DRAFT-' || field_definition_id, ?, target_domain,
                   target_field, canonical_field, label_zh, description_zh,
                   definition_status, data_type, required, enabled, sensitivity,
                   semantic_fingerprint, origin, source_mapping_item_id, display_order
              FROM standard_field_definition
             WHERE catalog_version_id = ?
            """,
            (catalog_id, active["catalog_version_id"]),
        )
        connection.execute(
            """
            INSERT INTO standard_field_alias(
                field_alias_id, catalog_version_id, field_definition_id,
                alias, normalized_alias, alias_type
            )
            SELECT 'SFA-DRAFT-' || field_alias_id, ?, 'SFD-DRAFT-' || field_definition_id,
                   alias, normalized_alias, alias_type
              FROM standard_field_alias
             WHERE catalog_version_id = ?
            """,
            (catalog_id, active["catalog_version_id"]),
        )
        request_id = f"SFCR-{catalog_id}-{proposed_domain}-{proposed_field}"
        connection.execute(
            """
            INSERT INTO standard_field_change_request(
                change_request_id, catalog_version_id, proposed_domain,
                proposed_field, label_zh, definition_zh, data_type,
                business_example, reuse_rationale, candidate_fields_json,
                source_context_json, requested_by, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
            """,
            (
                request_id,
                catalog_id,
                proposed_domain,
                proposed_field,
                label_zh,
                definition_zh,
                data_type,
                business_example,
                reuse_rationale,
                json.dumps(list(candidate_fields), ensure_ascii=False),
                json.dumps(source_context, ensure_ascii=False, sort_keys=True),
                requested_by,
            ),
        )
        return {"catalog_version_id": catalog_id, "change_request_id": request_id, "status": "DRAFT"}

    def approve_change_request(self, change_request_id: str, approved_by: str) -> dict:
        """Add an approved controlled field to its draft catalog only."""

        if not approved_by:
            raise StandardFieldCatalogError("STANDARD_FIELD_APPROVER_REQUIRED")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            request = connection.execute(
                "SELECT * FROM standard_field_change_request WHERE change_request_id = ?",
                (change_request_id,),
            ).fetchone()
            if request is None:
                raise StandardFieldCatalogError("STANDARD_FIELD_CHANGE_REQUEST_NOT_FOUND")
            if request["status"] != "PENDING":
                raise StandardFieldCatalogError("STANDARD_FIELD_CHANGE_REQUEST_NOT_PENDING")
            catalog = connection.execute(
                "SELECT status FROM standard_field_catalog_version WHERE catalog_version_id = ?",
                (request["catalog_version_id"],),
            ).fetchone()
            if catalog is None or catalog["status"] != "DRAFT":
                raise StandardFieldCatalogError("STANDARD_FIELD_CATALOG_NOT_DRAFT")
            path = f"{request['proposed_domain']}.{request['proposed_field']}"
            fingerprint = hashlib.sha256(f"{request['definition_zh'].strip().lower()}|{request['data_type'].strip().lower()}|".encode("utf-8")).hexdigest()
            try:
                connection.execute(
                    """
                    INSERT INTO standard_field_definition(
                        field_definition_id, catalog_version_id, target_domain,
                        target_field, canonical_field, label_zh, description_zh,
                        definition_status, data_type, required, enabled, sensitivity,
                        semantic_fingerprint, origin, display_order
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'APPROVED', ?, 0, 1,
                              'INTERNAL', ?, 'CONTROLLED_ADDITION', ?)
                    """,
                    (
                        f"SFD-{change_request_id}",
                        request["catalog_version_id"],
                        request["proposed_domain"],
                        request["proposed_field"],
                        request["proposed_field"],
                        request["label_zh"],
                        request["definition_zh"],
                        request["data_type"],
                        fingerprint,
                        connection.execute(
                            "SELECT COALESCE(MAX(display_order), -1) + 1 FROM standard_field_definition WHERE catalog_version_id = ?",
                            (request["catalog_version_id"],),
                        ).fetchone()[0],
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise StandardFieldCatalogError("STANDARD_FIELD_SEMANTIC_CONFLICT") from error
            connection.execute(
                """UPDATE standard_field_change_request
                      SET status = 'APPROVED', approved_by = ?, approved_at = CURRENT_TIMESTAMP
                    WHERE change_request_id = ?""",
                (approved_by, change_request_id),
            )
            connection.commit()
        return {"change_request_id": change_request_id, "status": "APPROVED", "path": path}

    def activate_catalog(self, catalog_version_id: str, approved_by: str) -> dict:
        """Activate a validated draft.  Existing preview tokens are version-bound."""

        if not approved_by:
            raise StandardFieldCatalogError("STANDARD_FIELD_APPROVER_REQUIRED")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            catalog = connection.execute(
                "SELECT * FROM standard_field_catalog_version WHERE catalog_version_id = ?",
                (catalog_version_id,),
            ).fetchone()
            if catalog is None:
                raise StandardFieldCatalogError("STANDARD_FIELD_CATALOG_NOT_FOUND")
            if catalog["status"] != "DRAFT":
                raise StandardFieldCatalogError("STANDARD_FIELD_CATALOG_NOT_DRAFT")
            validation_errors = self._validation_errors(connection, catalog_version_id)
            if validation_errors:
                raise StandardFieldCatalogError("STANDARD_FIELD_CATALOG_INVALID")
            connection.execute("UPDATE standard_field_catalog_version SET status = 'RETIRED' WHERE status = 'ACTIVE'")
            connection.execute(
                """UPDATE standard_field_catalog_version
                      SET status = 'ACTIVE', approved_by = ?, activated_at = CURRENT_TIMESTAMP
                    WHERE catalog_version_id = ?""",
                (approved_by, catalog_version_id),
            )
            connection.commit()
        return {"catalog_version_id": catalog_version_id, "status": "ACTIVE"}

    @staticmethod
    def _validation_errors(connection: sqlite3.Connection, catalog_version_id: str) -> list[str]:
        duplicate_aliases = connection.execute(
            """SELECT normalized_alias FROM standard_field_alias
               WHERE catalog_version_id = ? GROUP BY normalized_alias HAVING COUNT(*) > 1""",
            (catalog_version_id,),
        ).fetchall()
        pending_requests = connection.execute(
            """SELECT COUNT(*) FROM standard_field_change_request
               WHERE catalog_version_id = ? AND status = 'PENDING'""",
            (catalog_version_id,),
        ).fetchone()[0]
        errors = ["STANDARD_ALIAS_CONFLICT" for _ in duplicate_aliases]
        if pending_requests:
            errors.append("STANDARD_FIELD_CHANGE_REQUEST_PENDING")
        return errors
