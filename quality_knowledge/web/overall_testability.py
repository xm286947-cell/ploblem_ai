"""Controlled Overall product testability adapter.

This module is test-only infrastructure owned by the Overall platform. It is
disabled by default and never changes production business semantics. Provision
uses existing product service boundaries; reset restores only explicitly
isolated test-state paths captured at startup.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException
from openpyxl import Workbook

from quality_knowledge.p0.intake_service import P0IntakeError, P0IntakeService
from services.hardware_case_contract import HardwareCaseContractError


_FIXTURE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class OverallTestabilityError(RuntimeError):
    """Stable error raised by the controlled testability boundary."""


@dataclass(frozen=True)
class _StateSnapshot:
    path: Path
    backup: Path
    existed: bool
    is_dir: bool
    sidecars: tuple[tuple[Path, Path], ...] = ()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _canonical_payload_hash(payload: dict[str, Any]) -> str:
    body = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


class OverallTestabilityAdapter:
    """Provision/assert/reset adapter for an isolated Overall test runtime.

    Important boundaries:
    - product writes are made only through existing application services;
    - reset is file-level restoration of an isolated startup snapshot;
    - direct SQL and direct repository writes do not exist in this module;
    - one fixture is active at a time, making repeated runs deterministic.
    """

    def __init__(
        self,
        *,
        state_root: str | Path,
        token: str,
        mutable_paths: list[str | Path],
        p0_repository: Any,
        hardware_case_service: Any,
        restore_hooks: list[Callable[[], Any]] | None = None,
    ) -> None:
        self.state_root = Path(state_root).resolve()
        self.token = str(token or "")
        if not self.token:
            raise OverallTestabilityError("OVERALL_TESTABILITY_TOKEN_REQUIRED")
        self.state_root.mkdir(parents=True, exist_ok=True)
        self._baseline_root = self.state_root / ".overall_testability_baseline"
        if self._baseline_root.exists():
            shutil.rmtree(self._baseline_root)
        self._baseline_root.mkdir(parents=True, exist_ok=True)
        self._scratch_root = self.state_root / ".overall_testability_scratch"
        self._scratch_root.mkdir(parents=True, exist_ok=True)

        normalized: list[Path] = []
        for raw in mutable_paths:
            path = Path(raw).resolve()
            if not _is_relative_to(path, self.state_root):
                raise OverallTestabilityError(
                    f"OVERALL_TESTABILITY_PATH_OUTSIDE_STATE_ROOT:{path}"
                )
            if path in {self._baseline_root, self._scratch_root}:
                continue
            if path not in normalized:
                normalized.append(path)
        if not normalized:
            raise OverallTestabilityError("OVERALL_TESTABILITY_MUTABLE_PATH_REQUIRED")

        self._p0_repository = p0_repository
        self._hardware_case_service = hardware_case_service
        self._restore_hooks = tuple(restore_hooks or ())
        self._lock = threading.RLock()
        self._snapshots = self._capture_startup_state(normalized)
        self._fixtures: dict[str, dict[str, Any]] = {}

    def _capture_startup_state(self, paths: list[Path]) -> list[_StateSnapshot]:
        snapshots: list[_StateSnapshot] = []
        for index, path in enumerate(paths):
            backup = self._baseline_root / f"{index:02d}-{path.name}"
            existed = path.exists()
            is_dir = path.is_dir() if existed else path.suffix == ""
            sidecars: list[tuple[Path, Path]] = []
            if existed:
                if is_dir:
                    shutil.copytree(path, backup)
                else:
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, backup)
                    for suffix in ("-wal", "-shm", "-journal"):
                        sidecar = Path(str(path) + suffix)
                        if not sidecar.exists():
                            continue
                        sidecar_backup = Path(str(backup) + suffix)
                        shutil.copy2(sidecar, sidecar_backup)
                        sidecars.append((sidecar, sidecar_backup))
            snapshots.append(
                _StateSnapshot(
                    path=path,
                    backup=backup,
                    existed=existed,
                    is_dir=is_dir,
                    sidecars=tuple(sidecars),
                )
            )
        return snapshots

    @staticmethod
    def _remove_sqlite_sidecars(path: Path) -> None:
        if path.suffix:
            for suffix in ("-wal", "-shm", "-journal"):
                sidecar = Path(str(path) + suffix)
                if sidecar.exists():
                    sidecar.unlink()

    def _restore_startup_state(self) -> None:
        for snapshot in self._snapshots:
            path = snapshot.path
            if path.exists():
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            self._remove_sqlite_sidecars(path)
            if not snapshot.existed:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            if snapshot.is_dir:
                shutil.copytree(snapshot.backup, path)
            else:
                shutil.copy2(snapshot.backup, path)
                for sidecar, backup in snapshot.sidecars:
                    sidecar.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, sidecar)

        for hook in self._restore_hooks:
            try:
                hook()
            except Exception as error:
                raise OverallTestabilityError(
                    "OVERALL_TESTABILITY_RESTORE_HOOK_FAILED:"
                    + type(error).__name__
                ) from error

    @staticmethod
    def _validate_fixture_id(fixture_id: str) -> str:
        value = str(fixture_id or "").strip()
        if not _FIXTURE_ID.fullmatch(value):
            raise OverallTestabilityError("OVERALL_FIXTURE_ID_INVALID")
        return value

    def reset(self, fixture_id: str) -> dict[str, Any]:
        fixture_id = self._validate_fixture_id(fixture_id)
        with self._lock:
            self._restore_startup_state()
            self._fixtures.clear()
            return {
                "fixture_id": fixture_id,
                "state": "RESET",
                "clean": True,
                "repeatable": True,
                "isolated": True,
                "production_data_touch": False,
            }

    def _source_workbook(self, fixture_id: str, payload: dict[str, Any]) -> Path:
        rows = payload.get("rows")
        if not isinstance(rows, list) or not rows or not all(
            isinstance(item, dict) for item in rows
        ):
            raise OverallTestabilityError("SOURCE_CONTEXT_ROWS_REQUIRED")
        headers = payload.get("headers")
        if headers is None:
            headers = []
            for row in rows:
                for key in row:
                    name = str(key)
                    if name not in headers:
                        headers.append(name)
        if not isinstance(headers, list) or not headers:
            raise OverallTestabilityError("SOURCE_CONTEXT_HEADERS_REQUIRED")
        header_names = [str(item) for item in headers]

        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = str(payload.get("sheet") or "Data")
        worksheet.append(header_names)
        for row in rows:
            worksheet.append([row.get(name) for name in header_names])

        handle = tempfile.NamedTemporaryFile(
            prefix=f"{fixture_id}-",
            suffix=".xlsx",
            dir=self._scratch_root,
            delete=False,
        )
        path = Path(handle.name)
        handle.close()
        workbook.save(path)
        workbook.close()
        return path

    def _provision_source_context(
        self, fixture_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if self._p0_repository is None:
            raise OverallTestabilityError("SOURCE_CONTEXT_SERVICE_UNAVAILABLE")
        workbook = self._source_workbook(fixture_id, payload)
        try:
            intake = P0IntakeService(self._p0_repository)
            preview = intake.preview(
                workbook,
                str(payload.get("business_type") or "PLC").upper(),
                str(payload.get("sheet") or "") or None,
            )
            result = intake.confirm(str(preview["preview_token"]))
        except P0IntakeError as error:
            raise OverallTestabilityError(str(error)) from error
        finally:
            workbook.unlink(missing_ok=True)

        knowledge_ids: list[str] = []
        business_issue_ids: list[str] = []
        business_type = str(payload.get("business_type") or "PLC").upper()
        for row in preview.get("rows") or []:
            candidate = row.get("normalized_candidate") or {}
            issue_id = str(
                (candidate.get("ISSUE_FACT") or {}).get("business_issue_id") or ""
            ).strip()
            if not issue_id:
                continue
            business_issue_ids.append(issue_id)
            knowledge_ids.append(P0IntakeService._knowledge_id(business_type, issue_id))
        return {
            "business_type": business_type,
            "business_issue_ids": business_issue_ids,
            "knowledge_ids": knowledge_ids,
            "preview_token": preview.get("preview_token"),
            "import_batch_id": result.get("import_batch_id"),
            "outcome": result.get("outcome"),
        }

    def _provision_hardware_tree(
        self, payload: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if self._hardware_case_service is None:
            raise OverallTestabilityError("HARDWARE_TREE_SERVICE_UNAVAILABLE")
        node_ids: list[str] = []
        tree_types: set[str] = set()
        try:
            for raw in payload:
                if not isinstance(raw, dict):
                    raise OverallTestabilityError("HARDWARE_TREE_NODE_INVALID")
                saved = self._hardware_case_service.save_tree_node(dict(raw))
                node_ids.append(str(saved.get("node_id") or raw.get("node_id") or ""))
                tree_types.add(str(raw.get("tree_type") or ""))
        except HardwareCaseContractError as error:
            raise OverallTestabilityError(str(error)) from error
        return {
            "node_ids": [item for item in node_ids if item],
            "tree_types": sorted(item for item in tree_types if item),
        }

    def _provision_hardware_cases(
        self, payload: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if self._hardware_case_service is None:
            raise OverallTestabilityError("HARDWARE_CASE_SERVICE_UNAVAILABLE")
        case_ids: list[str] = []
        try:
            for raw in payload:
                if not isinstance(raw, dict):
                    raise OverallTestabilityError("HARDWARE_CASE_FIXTURE_INVALID")
                saved = self._hardware_case_service.create_case(dict(raw))
                case_ids.append(str(saved.get("case_id") or raw.get("case_id") or ""))
        except HardwareCaseContractError as error:
            raise OverallTestabilityError(str(error)) from error
        return {"case_ids": [item for item in case_ids if item]}

    def provision(self, fixture_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        fixture_id = self._validate_fixture_id(fixture_id)
        if not isinstance(payload, dict):
            raise OverallTestabilityError("OVERALL_FIXTURE_PAYLOAD_INVALID")
        with self._lock:
            self._restore_startup_state()
            self._fixtures.clear()
            provisioned: dict[str, Any] = {}

            source_context = payload.get("source_context")
            if source_context is not None:
                if not isinstance(source_context, dict):
                    raise OverallTestabilityError("SOURCE_CONTEXT_PAYLOAD_INVALID")
                provisioned["source_context"] = self._provision_source_context(
                    fixture_id, source_context
                )

            hardware_nodes = payload.get("hardware_tree_nodes") or []
            if hardware_nodes:
                if not isinstance(hardware_nodes, list):
                    raise OverallTestabilityError("HARDWARE_TREE_PAYLOAD_INVALID")
                provisioned["hardware_tree"] = self._provision_hardware_tree(
                    hardware_nodes
                )

            hardware_cases = payload.get("hardware_cases") or []
            if hardware_cases:
                if not isinstance(hardware_cases, list):
                    raise OverallTestabilityError("HARDWARE_CASE_PAYLOAD_INVALID")
                provisioned["hardware_cases"] = self._provision_hardware_cases(
                    hardware_cases
                )

            record = {
                "fixture_id": fixture_id,
                "payload_sha256": _canonical_payload_hash(payload),
                "provisioned": provisioned,
            }
            self._fixtures[fixture_id] = record
            return self.assert_state(fixture_id)

    def assert_state(self, fixture_id: str) -> dict[str, Any]:
        fixture_id = self._validate_fixture_id(fixture_id)
        with self._lock:
            record = self._fixtures.get(fixture_id)
            if record is None:
                raise OverallTestabilityError("OVERALL_FIXTURE_NOT_PROVISIONED")

            checks: dict[str, Any] = {}
            source = record["provisioned"].get("source_context")
            if source is not None:
                missing = [
                    knowledge_id
                    for knowledge_id in source.get("knowledge_ids") or []
                    if self._p0_repository.get_issue(knowledge_id) is None
                ]
                checks["source_context"] = {
                    "passed": not missing,
                    "missing_knowledge_ids": missing,
                }

            hardware_tree = record["provisioned"].get("hardware_tree")
            if hardware_tree is not None:
                expected = set(hardware_tree.get("node_ids") or [])
                actual: set[str] = set()
                for tree_type in hardware_tree.get("tree_types") or []:
                    tree = self._hardware_case_service.get_tree(tree_type)
                    actual.update(
                        str(node.get("node_id") or "")
                        for node in tree.get("nodes") or []
                    )
                checks["hardware_tree"] = {
                    "passed": expected.issubset(actual),
                    "missing_node_ids": sorted(expected - actual),
                }

            hardware_cases = record["provisioned"].get("hardware_cases")
            if hardware_cases is not None:
                missing_cases: list[str] = []
                for case_id in hardware_cases.get("case_ids") or []:
                    try:
                        self._hardware_case_service.get_case(
                            case_id, role="MAINTAINER", historical=True
                        )
                    except HardwareCaseContractError:
                        missing_cases.append(case_id)
                checks["hardware_cases"] = {
                    "passed": not missing_cases,
                    "missing_case_ids": missing_cases,
                }

            passed = all(item.get("passed") is True for item in checks.values())
            if not checks:
                passed = True
            return {
                **record,
                "state": "READY" if passed else "ASSERT_FAILED",
                "passed": passed,
                "checks": checks,
                "idempotent": True,
                "repeatable": True,
                "isolated": True,
                "production_data_touch": False,
            }

    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "contract_version": "overall-testability/v1",
            "state_root": str(self.state_root),
            "mutable_path_count": len(self._snapshots),
            "restore_hook_count": len(self._restore_hooks),
            "direct_db_write": False,
            "direct_repository_write": False,
            "production_data_touch": False,
        }


def create_overall_testability_router(
    adapter: OverallTestabilityAdapter,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/v2/testability",
        tags=["overall-testability"],
        include_in_schema=False,
    )

    def authorize(value: str | None) -> None:
        supplied = str(value or "")
        if not supplied or not hmac.compare_digest(supplied, adapter.token):
            raise HTTPException(status_code=403, detail="OVERALL_TESTABILITY_FORBIDDEN")

    def failure(error: OverallTestabilityError) -> HTTPException:
        return HTTPException(status_code=409, detail=str(error))

    @router.get("/status")
    def status(
        x_overall_testability_token: str | None = Header(
            default=None, alias="X-Overall-Testability-Token"
        ),
    ) -> dict[str, Any]:
        authorize(x_overall_testability_token)
        return adapter.status()

    @router.post("/fixtures/{fixture_id}/provision")
    def provision(
        fixture_id: str,
        payload: dict[str, Any],
        x_overall_testability_token: str | None = Header(
            default=None, alias="X-Overall-Testability-Token"
        ),
    ) -> dict[str, Any]:
        authorize(x_overall_testability_token)
        try:
            return adapter.provision(fixture_id, payload)
        except OverallTestabilityError as error:
            raise failure(error) from error

    @router.get("/fixtures/{fixture_id}/assert")
    def assert_state(
        fixture_id: str,
        x_overall_testability_token: str | None = Header(
            default=None, alias="X-Overall-Testability-Token"
        ),
    ) -> dict[str, Any]:
        authorize(x_overall_testability_token)
        try:
            return adapter.assert_state(fixture_id)
        except OverallTestabilityError as error:
            raise failure(error) from error

    @router.post("/fixtures/{fixture_id}/reset")
    def reset(
        fixture_id: str,
        x_overall_testability_token: str | None = Header(
            default=None, alias="X-Overall-Testability-Token"
        ),
    ) -> dict[str, Any]:
        authorize(x_overall_testability_token)
        try:
            return adapter.reset(fixture_id)
        except OverallTestabilityError as error:
            raise failure(error) from error

    return router
