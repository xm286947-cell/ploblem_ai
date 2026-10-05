"""Rebuildable read projection over Formal Hardware Knowledge.

Unified Knowledge remains the source of truth.  This module selects eligible
local promotion identities, resolves their objects only through the frozen
HardwareCaseKnowledgeAdapter public contract, and atomically materializes a
SQLite read model beneath the Persistent Data Root's rebuildable directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.hardware_asset_repository import (
    CandidateAssetRepository,
    CandidateAssetRepositoryError,
)
from services.hardware_case_knowledge_adapter import (
    HardwareCaseKnowledgeAdapter,
    HardwareKnowledgeAdapterError,
)
from services.hardware_r1_golden_knowledge_bridge import FIXED_KNOWLEDGE_REVISION


CONSUMPTION_CONTRACT_VERSION = "hardware-knowledge-consumption/v1"
PROJECTION_SCHEMA_VERSION = 1
PROJECTION_FILENAME = "hardware_knowledge_consumption.db"
FORMAL_KNOWLEDGE_OBJECT_VERSION = "hardware-case-knowledge-object/v1"

FIELD_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("title", 100),
    ("symptom", 90),
    ("root_cause", 90),
    ("failure_mechanism", 85),
    ("engineering_rule", 80),
    ("design_constraint", 80),
    ("diagnostic_clue", 75),
    ("verification_method", 70),
    ("actions", 70),
    ("interface", 65),
    ("signal", 65),
    ("key_parameters", 60),
    ("device_refs", 60),
    ("occurrence_condition", 55),
    ("analysis_process", 50),
    ("verification_result", 50),
    ("conclusion", 50),
    ("applicability", 45),
)

SCENE_PRIORITY_FIELDS: dict[str, frozenset[str]] = {
    "MARKET_ISSUE": frozenset(
        {"symptom", "occurrence_condition", "failure_mode"}
    ),
    "RND_DIAGNOSIS": frozenset(
        {"symptom", "diagnostic_clue", "failure_mechanism", "verification_method"}
    ),
    "DEVICE_RISK": frozenset(
        {"device_refs", "interface", "signal", "key_parameters"}
    ),
}

_TEXT_FIELDS = (
    "title",
    "symptom",
    "occurrence_condition",
    "failure_mode",
    "root_cause",
    "failure_mechanism",
    "analysis_process",
    "actions",
    "verification_result",
    "engineering_rule",
    "design_constraint",
    "diagnostic_clue",
    "verification_method",
    "applicability",
    "conclusion",
    "interface",
    "signal",
)
_JSON_FIELDS = ("key_parameters", "device_refs", "evidence_refs")
_ALL_FIELDS = (
    "knowledge_id",
    "public_ref",
    "business_case_id",
    *_TEXT_FIELDS,
    *_JSON_FIELDS,
    "source_domain",
    "source_object_type",
    "formal_revision",
    "formal_status",
    "formal_object_hash",
    "projected_at",
)
_SCALAR_FIELDS = tuple(name for name in _ALL_FIELDS if name not in _JSON_FIELDS)


class HardwareKnowledgeConsumptionError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def normalize_search_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _formal_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(dict(value)).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _field_value(group: Any, name: str) -> str | None:
    if not isinstance(group, Mapping):
        return None
    item = group.get(name)
    if item is None:
        return None
    if isinstance(item, Mapping):
        value = item.get("value")
    else:
        value = item
    if value is None:
        return None
    if not isinstance(value, str):
        raise HardwareKnowledgeConsumptionError("FORMAL_OBJECT_FIELD_INVALID")
    return value


def _optional_source_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HardwareKnowledgeConsumptionError("FORMAL_OBJECT_FIELD_INVALID")
    return value


def _device_refs(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Project only explicitly supplied device/context data; never infer specs."""
    refs: list[dict[str, Any]] = []
    explicit_refs = context.get("device_refs")
    if isinstance(explicit_refs, list):
        for source in explicit_refs:
            if not isinstance(source, Mapping):
                raise HardwareKnowledgeConsumptionError("FORMAL_OBJECT_FIELD_INVALID")
            raw = dict(source)
            values = {
                "category": raw.get("category"),
                "generic_name_or_series": raw.get("generic_name_or_series"),
                "internal_material_no": raw.get("internal_material_no"),
                "manufacturer": raw.get("manufacturer"),
                "manufacturer_part_no": raw.get("manufacturer_part_no"),
            }
            for key, value in values.items():
                if value is not None and not isinstance(value, str):
                    raise HardwareKnowledgeConsumptionError("FORMAL_OBJECT_FIELD_INVALID")
            evidence = raw.get("evidence_refs")
            if evidence is not None and (
                not isinstance(evidence, list)
                or any(not isinstance(item, str) for item in evidence)
            ):
                raise HardwareKnowledgeConsumptionError("FORMAL_OBJECT_FIELD_INVALID")
            refs.append(
                {
                    **values,
                    "evidence_refs": list(evidence or []),
                    "status": "EXPLICIT"
                    if any(value not in (None, "") for value in values.values())
                    else "MISSING",
                }
            )
        return refs

    # The frozen KO has explicit component/device and peer/load fields, but not
    # vendor/MPN fields. Map their explicit value only; leave unknown attributes
    # null and do not attach unrelated object-level evidence to a device.
    for source_name in ("component_or_device", "peer_device_or_load"):
        source = context.get(source_name)
        if not isinstance(source, Mapping):
            continue
        name = source.get("value")
        if name in (None, ""):
            continue
        if not isinstance(name, str):
            raise HardwareKnowledgeConsumptionError("FORMAL_OBJECT_FIELD_INVALID")
        explicit_generic = source.get("generic_name_or_series")
        if explicit_generic is not None and not isinstance(explicit_generic, str):
            raise HardwareKnowledgeConsumptionError("FORMAL_OBJECT_FIELD_INVALID")
        values = {
            "category": _optional_source_text(source.get("category")),
            "generic_name_or_series": explicit_generic or name,
            "internal_material_no": _optional_source_text(
                source.get("internal_material_no")
            ),
            "manufacturer": _optional_source_text(source.get("manufacturer")),
            "manufacturer_part_no": _optional_source_text(
                source.get("manufacturer_part_no")
            ),
        }
        evidence = source.get("evidence_refs")
        if evidence is not None and (
            not isinstance(evidence, list)
            or any(not isinstance(item, str) for item in evidence)
        ):
            raise HardwareKnowledgeConsumptionError("FORMAL_OBJECT_FIELD_INVALID")
        refs.append(
            {
                **values,
                "evidence_refs": list(evidence or []),
                "status": "EXPLICIT",
            }
        )
    return refs


def _validate_projection_row(row: Mapping[str, Any]) -> None:
    schema_version = row.get("projection_schema_version")
    revision = row.get("formal_revision")
    if (
        schema_version != PROJECTION_SCHEMA_VERSION
        or any(not isinstance(row.get(name), str) or not row.get(name) for name in (
            "knowledge_id",
            "public_ref",
            "business_case_id",
            "source_domain",
            "source_object_type",
            "formal_status",
            "projected_at",
        ))
        or row.get("source_domain") != "HARDWARE_CASE"
        or row.get("source_object_type") != "HARDWARE_CASE"
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 1
        or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("formal_object_hash") or ""))
    ):
        raise HardwareKnowledgeConsumptionError("PROJECTION_ROW_INVALID")
    if any(
        row.get(name) is not None and not isinstance(row.get(name), str)
        for name in _TEXT_FIELDS
    ):
        raise HardwareKnowledgeConsumptionError("PROJECTION_ROW_INVALID")
    if (
        not isinstance(row.get("key_parameters"), list)
        or any(not isinstance(item, Mapping) for item in row["key_parameters"])
        or not isinstance(row.get("evidence_refs"), list)
        or any(not isinstance(item, str) for item in row["evidence_refs"])
        or not isinstance(row.get("device_refs"), list)
    ):
        raise HardwareKnowledgeConsumptionError("PROJECTION_ROW_INVALID")
    required_device_fields = {
        "category",
        "generic_name_or_series",
        "internal_material_no",
        "manufacturer",
        "manufacturer_part_no",
        "evidence_refs",
        "status",
    }
    for item in row["device_refs"]:
        if (
            not isinstance(item, Mapping)
            or set(item) != required_device_fields
            or any(
                item.get(name) is not None and not isinstance(item.get(name), str)
                for name in required_device_fields - {"evidence_refs", "status"}
            )
            or not isinstance(item.get("evidence_refs"), list)
            or any(not isinstance(value, str) for value in item["evidence_refs"])
            or item.get("status") not in {"EXPLICIT", "MISSING"}
        ):
            raise HardwareKnowledgeConsumptionError("PROJECTION_ROW_INVALID")


def _project_formal_object(
    *,
    asset_candidate_id: str,
    promotion: Mapping[str, Any],
    candidate: Mapping[str, Any],
    formal_object: Mapping[str, Any],
) -> dict[str, Any]:
    knowledge_id = str(promotion.get("knowledge_id") or "").strip()
    public_ref = str(promotion.get("public_ref") or "").strip()
    business_case_id = str(candidate.get("business_case_id") or "").strip()
    content = formal_object.get("content")
    if (
        not knowledge_id
        or not public_ref
        or not business_case_id
        or str(candidate.get("candidate_id") or "") != asset_candidate_id
        or formal_object.get("knowledge_id") != knowledge_id
        or formal_object.get("candidate_ref") != public_ref
        or formal_object.get("domain") != "HARDWARE_CASE"
        or formal_object.get("object_type") != "HARDWARE_CASE"
        or formal_object.get("status") != "ACTIVE"
        or int(formal_object.get("revision") or 0) != FIXED_KNOWLEDGE_REVISION
        or not isinstance(content, Mapping)
        or content.get("contract_version") != FORMAL_KNOWLEDGE_OBJECT_VERSION
    ):
        raise HardwareKnowledgeConsumptionError("FORMAL_OBJECT_IDENTITY_MISMATCH")

    expected_public_ref = HardwareCaseKnowledgeAdapter.public_ref(
        business_case_id, FIXED_KNOWLEDGE_REVISION
    )
    identity = content.get("identity")
    source_fact = content.get("source_fact")
    context = content.get("engineering_context")
    observed = content.get("observed_problem")
    analysis = content.get("engineering_analysis")
    resolution = content.get("engineering_resolution")
    reusable = content.get("reusable_knowledge")
    formal_evidence = formal_object.get("evidence_refs")
    local_evidence = candidate.get("evidence_refs")
    content_evidence = content.get("evidence")

    if (
        public_ref != expected_public_ref
        or str(promotion.get("business_case_id") or "") != business_case_id
        or str(promotion.get("source_id") or "")
        != str(candidate.get("source_id") or "")
        or not isinstance(identity, Mapping)
        or str(identity.get("business_case_id") or "") != business_case_id
        or not isinstance(source_fact, Mapping)
        or str(source_fact.get("source_id") or "")
        != str(candidate.get("source_id") or "")
        or str(source_fact.get("business_case_id") or "") != business_case_id
        or not isinstance(context, Mapping)
        or not isinstance(observed, Mapping)
        or not isinstance(analysis, Mapping)
        or not isinstance(resolution, Mapping)
        or not isinstance(reusable, Mapping)
        or not isinstance(formal_evidence, list)
        or any(not isinstance(item, str) for item in formal_evidence)
        or not isinstance(local_evidence, list)
        or not isinstance(content_evidence, list)
    ):
        raise HardwareKnowledgeConsumptionError("FORMAL_OBJECT_IDENTITY_MISMATCH")

    local_evidence_ids = [
        str(item.get("evidence_id") or "")
        for item in local_evidence
        if isinstance(item, Mapping)
    ]
    if (
        len(local_evidence_ids) != len(local_evidence)
        or len(set(local_evidence_ids)) != len(local_evidence_ids)
        or len(set(formal_evidence)) != len(formal_evidence)
        or set(local_evidence_ids) != set(formal_evidence)
    ):
        raise HardwareKnowledgeConsumptionError("FORMAL_EVIDENCE_IDENTITY_MISMATCH")

    local_blocks = {
        str(item.get("block_id") or "")
        for item in local_evidence
        if isinstance(item, Mapping) and item.get("block_id")
    }
    content_blocks = [
        str(item.get("block_id") or "")
        for item in content_evidence
        if isinstance(item, Mapping)
    ]
    formal_blocks = set(content_blocks)
    if (
        len(content_blocks) != len(content_evidence)
        or any(not item for item in content_blocks)
        or len(formal_blocks) != len(content_blocks)
        or local_blocks != formal_blocks
    ):
        raise HardwareKnowledgeConsumptionError("FORMAL_EVIDENCE_IDENTITY_MISMATCH")

    parameters = context.get("key_parameters")
    if parameters is not None and not isinstance(parameters, list):
        raise HardwareKnowledgeConsumptionError("FORMAL_OBJECT_FIELD_INVALID")
    if parameters is None:
        parameters = []

    output: dict[str, Any] = {
        "projection_schema_version": PROJECTION_SCHEMA_VERSION,
        "knowledge_id": knowledge_id,
        "public_ref": public_ref,
        "business_case_id": business_case_id,
        "title": _optional_source_text(identity.get("raw_title")),
        "symptom": _field_value(observed, "symptom"),
        "occurrence_condition": _field_value(observed, "occurrence_condition"),
        "failure_mode": _field_value(observed, "failure_mode"),
        "root_cause": _field_value(analysis, "root_cause"),
        "failure_mechanism": _field_value(analysis, "failure_mechanism"),
        "analysis_process": _field_value(analysis, "analysis_process"),
        "actions": _field_value(resolution, "actions"),
        "verification_result": _field_value(resolution, "verification_result"),
        "engineering_rule": _field_value(reusable, "engineering_rule"),
        "design_constraint": _field_value(reusable, "design_constraint"),
        "diagnostic_clue": _field_value(reusable, "diagnostic_clue"),
        "verification_method": _field_value(reusable, "verification_method"),
        "applicability": _field_value(reusable, "applicability"),
        "conclusion": _field_value(reusable, "conclusion"),
        "interface": _field_value(context, "interface"),
        "signal": _field_value(context, "signal"),
        "key_parameters": list(parameters),
        "device_refs": _device_refs(context),
        "evidence_refs": list(formal_evidence),
        "source_domain": str(formal_object.get("domain")),
        "source_object_type": str(formal_object.get("object_type")),
        "formal_revision": int(formal_object["revision"]),
        "formal_status": str(formal_object.get("status")),
        "formal_object_hash": _formal_hash(formal_object),
        "projected_at": _utc_now(),
    }
    for name in _TEXT_FIELDS:
        if output[name] is not None and not isinstance(output[name], str):
            raise HardwareKnowledgeConsumptionError("FORMAL_OBJECT_FIELD_INVALID")
    return output


class HardwareKnowledgeConsumptionProjectionStore:
    """SQLite projection store; writes replace a staged, validated file atomically."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.RLock()

    def _read_connection(self) -> sqlite3.Connection:
        uri = self.db_path.resolve(strict=False).as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        column_sql = ",\n".join(
            f'"{name}" {("INTEGER" if name in {"projection_schema_version", "formal_revision"} else "TEXT")}'
            for name in (
                "projection_schema_version",
                *_SCALAR_FIELDS,
            )
        )
        connection.executescript(
            f"""
            CREATE TABLE hardware_knowledge_consumption_projection (
                {column_sql},
                key_parameters_json TEXT NOT NULL,
                device_refs_json TEXT NOT NULL,
                evidence_refs_json TEXT NOT NULL,
                PRIMARY KEY(knowledge_id)
            );
            CREATE INDEX idx_hw_knowledge_consumption_case
            ON hardware_knowledge_consumption_projection(business_case_id);
            CREATE TABLE hardware_knowledge_consumption_meta (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                projection_schema_version INTEGER NOT NULL,
                row_count INTEGER NOT NULL,
                last_rebuilt_at TEXT NOT NULL
            );
            """
        )

    def projection_status(self) -> dict[str, Any]:
        if not self.db_path.is_file():
            return {
                "contract_version": CONSUMPTION_CONTRACT_VERSION,
                "projection_schema_version": PROJECTION_SCHEMA_VERSION,
                "status": "MISSING",
                "row_count": 0,
                "last_rebuilt_at": None,
            }
        connection: sqlite3.Connection | None = None
        try:
            connection = self._read_connection()
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            required = {
                "hardware_knowledge_consumption_projection",
                "hardware_knowledge_consumption_meta",
            }
            if not required.issubset(tables):
                return {
                    "contract_version": CONSUMPTION_CONTRACT_VERSION,
                    "projection_schema_version": PROJECTION_SCHEMA_VERSION,
                    "status": "INCOMPATIBLE",
                    "row_count": 0,
                    "last_rebuilt_at": None,
                }
            projection_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(hardware_knowledge_consumption_projection)"
                )
            }
            metadata_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(hardware_knowledge_consumption_meta)"
                )
            }
            expected_projection_columns = {
                "projection_schema_version",
                *_SCALAR_FIELDS,
                *(name + "_json" for name in _JSON_FIELDS),
            }
            expected_metadata_columns = {
                "singleton",
                "projection_schema_version",
                "row_count",
                "last_rebuilt_at",
            }
            if (
                not expected_projection_columns.issubset(projection_columns)
                or not expected_metadata_columns.issubset(metadata_columns)
            ):
                return {
                    "contract_version": CONSUMPTION_CONTRACT_VERSION,
                    "projection_schema_version": PROJECTION_SCHEMA_VERSION,
                    "status": "INCOMPATIBLE",
                    "row_count": 0,
                    "last_rebuilt_at": None,
                }
            meta = connection.execute(
                "SELECT * FROM hardware_knowledge_consumption_meta WHERE singleton=1"
            ).fetchone()
            try:
                compatible = (
                    meta is not None
                    and int(meta["projection_schema_version"])
                    == PROJECTION_SCHEMA_VERSION
                )
            except (ValueError, TypeError):
                compatible = False
            if not compatible:
                return {
                    "contract_version": CONSUMPTION_CONTRACT_VERSION,
                    "projection_schema_version": PROJECTION_SCHEMA_VERSION,
                    "status": "INCOMPATIBLE",
                    "row_count": 0,
                    "last_rebuilt_at": None,
                }
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or str(integrity[0]).lower() != "ok":
                return {
                    "contract_version": CONSUMPTION_CONTRACT_VERSION,
                    "projection_schema_version": PROJECTION_SCHEMA_VERSION,
                    "status": "CORRUPT",
                    "row_count": 0,
                    "last_rebuilt_at": None,
                }
            count = connection.execute(
                "SELECT COUNT(*) FROM hardware_knowledge_consumption_projection"
            ).fetchone()[0]
            if int(count) != int(meta["row_count"]):
                return {
                    "contract_version": CONSUMPTION_CONTRACT_VERSION,
                    "projection_schema_version": PROJECTION_SCHEMA_VERSION,
                    "status": "CORRUPT",
                    "row_count": int(count),
                    "last_rebuilt_at": meta["last_rebuilt_at"],
                }
            return {
                "contract_version": CONSUMPTION_CONTRACT_VERSION,
                "projection_schema_version": PROJECTION_SCHEMA_VERSION,
                "status": "READY",
                "row_count": int(count),
                "last_rebuilt_at": meta["last_rebuilt_at"],
            }
        except (sqlite3.DatabaseError, ValueError, TypeError, IndexError):
            return {
                "contract_version": CONSUMPTION_CONTRACT_VERSION,
                "projection_schema_version": PROJECTION_SCHEMA_VERSION,
                "status": "CORRUPT",
                "row_count": 0,
                "last_rebuilt_at": None,
            }
        finally:
            if connection is not None:
                connection.close()

    def _read_all(self, *, missing_ok: bool = False) -> list[dict[str, Any]]:
        status = self.projection_status()
        if status["status"] == "MISSING" and missing_ok:
            return []
        if status["status"] != "READY":
            raise HardwareKnowledgeConsumptionError(
                "CONSUMPTION_PROJECTION_" + str(status["status"])
            )
        connection = self._read_connection()
        try:
            rows = connection.execute(
                "SELECT * FROM hardware_knowledge_consumption_projection "
                "ORDER BY knowledge_id"
            ).fetchall()
            return [self._decode_row(row) for row in rows]
        except (
            sqlite3.DatabaseError,
            ValueError,
            TypeError,
            IndexError,
            HardwareKnowledgeConsumptionError,
        ) as error:
            raise HardwareKnowledgeConsumptionError(
                "CONSUMPTION_PROJECTION_CORRUPT"
            ) from error
        finally:
            connection.close()

    @staticmethod
    def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
        result = {name: row[name] for name in _SCALAR_FIELDS}
        result["projection_schema_version"] = int(row["projection_schema_version"])
        result["formal_revision"] = int(row["formal_revision"])
        for name in _JSON_FIELDS:
            result[name] = json.loads(row[name + "_json"])
        _validate_projection_row(result)
        return result

    def list_all(self) -> list[dict[str, Any]]:
        return self._read_all()

    def get(self, knowledge_id: str) -> dict[str, Any] | None:
        wanted = str(knowledge_id or "").strip()
        if not wanted:
            raise HardwareKnowledgeConsumptionError("KNOWLEDGE_ID_REQUIRED")
        status = self.projection_status()
        if status["status"] != "READY":
            raise HardwareKnowledgeConsumptionError(
                "CONSUMPTION_PROJECTION_" + str(status["status"])
            )
        connection = self._read_connection()
        try:
            row = connection.execute(
                "SELECT * FROM hardware_knowledge_consumption_projection WHERE knowledge_id=?",
                (wanted,),
            ).fetchone()
            return None if row is None else self._decode_row(row)
        except (
            sqlite3.DatabaseError,
            ValueError,
            TypeError,
            IndexError,
            HardwareKnowledgeConsumptionError,
        ) as error:
            raise HardwareKnowledgeConsumptionError(
                "CONSUMPTION_PROJECTION_CORRUPT"
            ) from error
        finally:
            connection.close()

    def replace_all(self, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        materialized = [dict(row) for row in rows]
        seen: set[str] = set()
        for row in materialized:
            knowledge_id = str(row.get("knowledge_id") or "")
            if not knowledge_id or knowledge_id in seen:
                raise HardwareKnowledgeConsumptionError(
                    "PROJECTION_IDENTITY_DUPLICATE"
                )
            seen.add(knowledge_id)
            _validate_projection_row(row)

        with self._lock:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, stage_name = tempfile.mkstemp(
                prefix=self.db_path.name + ".staging-",
                suffix=".db",
                dir=self.db_path.parent,
            )
            os.close(descriptor)
            stage = Path(stage_name)
            connection: sqlite3.Connection | None = None
            try:
                connection = sqlite3.connect(stage)
                connection.row_factory = sqlite3.Row
                self._create_schema(connection)
                columns = ["projection_schema_version", *_SCALAR_FIELDS]
                sql_columns = [
                    "key_parameters_json",
                    "device_refs_json",
                    "evidence_refs_json",
                ]
                all_columns = columns + sql_columns
                placeholders = ",".join("?" for _ in all_columns)
                insert = (
                    "INSERT INTO hardware_knowledge_consumption_projection("
                    + ",".join(f'"{name}"' for name in all_columns)
                    + f") VALUES({placeholders})"
                )
                with connection:
                    for row in materialized:
                        values = [row.get(name) for name in columns]
                        values.extend(_canonical_json(row[name]) for name in _JSON_FIELDS)
                        connection.execute(insert, values)
                    rebuilt_at = _utc_now()
                    connection.execute(
                        "INSERT INTO hardware_knowledge_consumption_meta "
                        "(singleton,projection_schema_version,row_count,last_rebuilt_at) "
                        "VALUES(1,?,?,?)",
                        (PROJECTION_SCHEMA_VERSION, len(materialized), rebuilt_at),
                    )
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
                if integrity is None or str(integrity[0]).lower() != "ok":
                    raise HardwareKnowledgeConsumptionError("PROJECTION_STAGE_INVALID")
                connection.close()
                connection = None
                # Windows FlushFileBuffers requires a writable file handle even
                # though the staged SQLite payload is not modified here.
                with stage.open("rb+") as stream:
                    os.fsync(stream.fileno())
                os.replace(stage, self.db_path)
                try:
                    dir_fd = os.open(self.db_path.parent, os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except OSError:
                    pass
            except HardwareKnowledgeConsumptionError:
                raise
            except (OSError, sqlite3.DatabaseError, TypeError, ValueError) as error:
                raise HardwareKnowledgeConsumptionError(
                    "PROJECTION_REBUILD_FAILED"
                ) from error
            finally:
                if connection is not None:
                    connection.close()
                try:
                    stage.unlink()
                except FileNotFoundError:
                    pass
        return self.projection_status()


class HardwareKnowledgeConsumptionService:
    """Project locally verified Formal KOs and provide deterministic reads."""

    def __init__(
        self,
        projection_store: HardwareKnowledgeConsumptionProjectionStore,
        *,
        candidate_repository: CandidateAssetRepository | Any | None = None,
        knowledge_adapter: HardwareCaseKnowledgeAdapter | Any | None = None,
    ) -> None:
        self.store = projection_store
        self.assets = candidate_repository
        self.adapter = knowledge_adapter

    def projection_status(self) -> dict[str, Any]:
        return self.store.projection_status()

    def _require_projector(self) -> None:
        if self.assets is None or self.adapter is None:
            raise HardwareKnowledgeConsumptionError(
                "PROJECTION_BUILDER_NOT_CONFIGURED"
            )

    def _build_row(self, promotion: Mapping[str, Any]) -> dict[str, Any]:
        self._require_projector()
        candidate_id = str(promotion.get("asset_candidate_id") or "").strip()
        if (
            promotion.get("promotion_status") != "VERIFIED"
            or not candidate_id
            or not str(promotion.get("knowledge_id") or "").strip()
            or not str(promotion.get("public_ref") or "").strip()
        ):
            raise HardwareKnowledgeConsumptionError("FORMAL_OBJECT_NOT_ELIGIBLE")
        try:
            candidate = self.assets.get_candidate(candidate_id)
            if candidate is None:
                raise HardwareKnowledgeConsumptionError("CANDIDATE_NOT_FOUND")
            if candidate.get("promotion_status") != "VERIFIED":
                raise HardwareKnowledgeConsumptionError("PROMOTION_LEDGER_MISMATCH")
            formal_object = self.adapter.get_object(
                str(promotion["knowledge_id"]),
                expected_revision=FIXED_KNOWLEDGE_REVISION,
            )
        except CandidateAssetRepositoryError as error:
            raise HardwareKnowledgeConsumptionError(error.code) from error
        except HardwareKnowledgeAdapterError as error:
            raise HardwareKnowledgeConsumptionError(error.code) from error
        except HardwareKnowledgeConsumptionError:
            raise
        except Exception as error:
            code = str(getattr(error, "code", None) or "FORMAL_OBJECT_RESOLUTION_FAILED")
            raise HardwareKnowledgeConsumptionError(code) from error
        return _project_formal_object(
            asset_candidate_id=candidate_id,
            promotion=promotion,
            candidate=candidate,
            formal_object=formal_object,
        )

    def project_verified(self, asset_candidate_id: str) -> dict[str, Any]:
        self._require_projector()
        candidate_id = str(asset_candidate_id or "").strip()
        if not candidate_id:
            raise HardwareKnowledgeConsumptionError("CANDIDATE_ID_REQUIRED")
        try:
            promotion = self.assets.get_promotion_record(candidate_id)
        except CandidateAssetRepositoryError as error:
            raise HardwareKnowledgeConsumptionError(error.code) from error
        if promotion is None:
            raise HardwareKnowledgeConsumptionError("PROMOTION_NOT_VERIFIED")
        promotion = dict(promotion)
        promotion["asset_candidate_id"] = candidate_id
        row = self._build_row(promotion)
        rows = self.store._read_all(missing_ok=True)
        rows_by_id = {str(item["knowledge_id"]): item for item in rows}
        if row["knowledge_id"] in rows_by_id:
            existing = rows_by_id[row["knowledge_id"]]
            if existing.get("public_ref") != row.get("public_ref"):
                raise HardwareKnowledgeConsumptionError("PROJECTION_IDENTITY_CONFLICT")
        rows_by_id[row["knowledge_id"]] = row
        status = self.store.replace_all(
            [rows_by_id[key] for key in sorted(rows_by_id)]
        )
        return {
            "contract_version": CONSUMPTION_CONTRACT_VERSION,
            "asset_candidate_id": candidate_id,
            "knowledge_id": row["knowledge_id"],
            "projected": True,
            "projection_status": status,
        }

    def rebuild_all_verified(self) -> dict[str, Any]:
        self._require_projector()
        try:
            promotions = self.assets.list_promotion_records()
        except CandidateAssetRepositoryError as error:
            raise HardwareKnowledgeConsumptionError(error.code) from error
        eligible = [
            dict(item)
            for item in promotions
            if item.get("promotion_status") == "VERIFIED"
            and str(item.get("knowledge_id") or "").strip()
            and str(item.get("public_ref") or "").strip()
        ]
        rows: list[dict[str, Any]] = []
        for promotion in eligible:
            rows.append(self._build_row(promotion))
        rows.sort(key=lambda item: str(item["knowledge_id"]))
        status = self.store.replace_all(rows)
        return {
            "contract_version": CONSUMPTION_CONTRACT_VERSION,
            "projection_status": status,
            "eligible_promotion_count": len(eligible),
            "projected_count": len(rows),
            "skipped_incomplete_verified_count": sum(
                1
                for item in promotions
                if item.get("promotion_status") == "VERIFIED"
                and not (
                    str(item.get("knowledge_id") or "").strip()
                    and str(item.get("public_ref") or "").strip()
                )
            ),
        }

    def get(self, knowledge_id: str) -> dict[str, Any] | None:
        item = self.store.get(knowledge_id)
        if item is None:
            return None
        return {"contract_version": CONSUMPTION_CONTRACT_VERSION, **item}

    def search(
        self,
        text: str = "",
        *,
        knowledge_id: str | None = None,
        business_case_id: str | None = None,
        source_domain: str | None = None,
        source_object_type: str | None = None,
        interface: str | None = None,
        signal: str | None = None,
        device: str | None = None,
        scene: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        if int(limit) < 1 or int(limit) > 500:
            raise HardwareKnowledgeConsumptionError("SEARCH_LIMIT_INVALID")
        scene_key = str(scene or "").strip().upper()
        if scene_key and scene_key not in SCENE_PRIORITY_FIELDS:
            raise HardwareKnowledgeConsumptionError("SEARCH_SCENE_INVALID")
        query_text = normalize_search_text(text)
        query_terms = list(dict.fromkeys(
            term for term in query_text.split(" ") if term
        ))
        rows = self.store.list_all()
        filtered = [
            row
            for row in rows
            if self._matches_filters(
                row,
                knowledge_id=knowledge_id,
                business_case_id=business_case_id,
                source_domain=source_domain,
                source_object_type=source_object_type,
                interface=interface,
                signal=signal,
                device=device,
            )
        ]
        results: list[dict[str, Any]] = []
        for row in filtered:
            score = 0
            reasons: list[dict[str, Any]] = []
            matched_terms: set[str] = set()
            if query_terms:
                for field, weight in FIELD_WEIGHTS:
                    normalized_values = [
                        normalize_search_text(value)
                        for value in self._search_values(row, field)
                    ]
                    field_terms = [
                        term
                        for term in query_terms
                        if any(term in value for value in normalized_values)
                    ]
                    if field_terms:
                        score += weight
                        matched_terms.update(field_terms)
                        reasons.append(
                            {
                                "matched_field": field,
                                "match_type": "SUBSTRING",
                                "matched_text": " ".join(field_terms),
                                "weight": weight,
                            }
                        )
                # Multi-keyword search stays deterministic and conservative:
                # every normalized term must be supported somewhere in the
                # projected Formal Knowledge record.  Fields contribute their
                # configured weight at most once, even when several terms hit.
                if len(matched_terms) != len(query_terms):
                    continue
            scene_fields = SCENE_PRIORITY_FIELDS.get(scene_key, frozenset())
            scene_priority_score = sum(
                int(reason["weight"])
                for reason in reasons
                if reason["matched_field"] in scene_fields
            )
            results.append(
                {
                    "contract_version": CONSUMPTION_CONTRACT_VERSION,
                    **row,
                    "match_score": score,
                    "match_reasons": reasons,
                    "_scene_priority_score": scene_priority_score,
                }
            )
        results.sort(
            key=lambda item: (
                -int(item["_scene_priority_score"]),
                -int(item["match_score"]),
                str(item["knowledge_id"]),
            )
        )
        for item in results:
            item.pop("_scene_priority_score", None)
        return {
            "contract_version": CONSUMPTION_CONTRACT_VERSION,
            "results": results[: int(limit)],
        }

    @staticmethod
    def _matches_filters(
        row: Mapping[str, Any],
        *,
        knowledge_id: str | None,
        business_case_id: str | None,
        source_domain: str | None,
        source_object_type: str | None,
        interface: str | None,
        signal: str | None,
        device: str | None,
    ) -> bool:
        exact = (
            ("knowledge_id", knowledge_id, False),
            ("business_case_id", business_case_id, False),
            ("source_domain", source_domain, True),
            ("source_object_type", source_object_type, True),
            ("interface", interface, True),
            ("signal", signal, True),
        )
        for field, wanted, normalize in exact:
            if wanted is None or str(wanted).strip() == "":
                continue
            actual = str(row.get(field) or "")
            if normalize:
                if normalize_search_text(actual) != normalize_search_text(wanted):
                    return False
            elif actual != str(wanted):
                return False
        if device is not None and str(device).strip():
            expected = normalize_search_text(device)
            device_values = HardwareKnowledgeConsumptionService._search_values(
                row, "device_refs"
            )
            if not any(normalize_search_text(value) == expected for value in device_values):
                return False
        return True

    @staticmethod
    def _search_values(row: Mapping[str, Any], field: str) -> list[str]:
        if field == "key_parameters":
            values: list[str] = []
            for parameter in row.get("key_parameters") or []:
                if not isinstance(parameter, Mapping):
                    continue
                for name in sorted(parameter):
                    if name in {
                        "evidence_block_ids",
                        "evidence_refs",
                        "extraction_status",
                        "review_status",
                        "derived_from_fields",
                    }:
                        continue
                    values.extend(
                        HardwareKnowledgeConsumptionService._flatten_strings(
                            parameter[name]
                        )
                    )
            return values
        if field == "device_refs":
            names = (
                "category",
                "generic_name_or_series",
                "internal_material_no",
                "manufacturer",
                "manufacturer_part_no",
            )
            return [
                str(item[name])
                for item in row.get("device_refs") or []
                if isinstance(item, Mapping)
                for name in names
                if isinstance(item.get(name), str) and item[name]
            ]
        value = row.get(field)
        return [value] if isinstance(value, str) and value else []

    @staticmethod
    def _flatten_strings(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value] if value else []
        if isinstance(value, Mapping):
            output: list[str] = []
            for key in sorted(value):
                output.extend(HardwareKnowledgeConsumptionService._flatten_strings(value[key]))
            return output
        if isinstance(value, list):
            output = []
            for item in value:
                output.extend(HardwareKnowledgeConsumptionService._flatten_strings(item))
            return output
        return []


__all__ = [
    "CONSUMPTION_CONTRACT_VERSION",
    "FIELD_WEIGHTS",
    "FORMAL_KNOWLEDGE_OBJECT_VERSION",
    "PROJECTION_FILENAME",
    "PROJECTION_SCHEMA_VERSION",
    "SCENE_PRIORITY_FIELDS",
    "HardwareKnowledgeConsumptionError",
    "HardwareKnowledgeConsumptionProjectionStore",
    "HardwareKnowledgeConsumptionService",
    "normalize_search_text",
]
