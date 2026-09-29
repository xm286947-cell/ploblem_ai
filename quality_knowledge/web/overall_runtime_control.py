from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from runtime.binding import load_runtime_binding
from runtime.config.models import ModelProfilesConfig


_FORBIDDEN_SECRET_KEYS = {
    "api_key",
    "authorization",
    "bearer_token",
    "client_secret",
    "password",
    "secret",
    "token",
}
_DOMAIN_MODEL_CONFIG_ENVS = (
    "MAJOR_MODEL_CONFIG",
    "HARDWARE_CASE_MODEL_CONFIG",
    "STORAGE_MODEL_CONFIG",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if (
                normalized in _FORBIDDEN_SECRET_KEYS
                or normalized.endswith("_secret")
            ):
                safe[key] = "[REDACTED]"
            else:
                safe[key] = _safe_value(item)
        return safe
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    return value


def _find_literal_secret(value: Any, path: str = "$") -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if (
                normalized in _FORBIDDEN_SECRET_KEYS
                or normalized.endswith("_secret")
            ):
                return f"{path}.{key}"
            found = _find_literal_secret(item, f"{path}.{key}")
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _find_literal_secret(item, f"{path}[{index}]")
            if found:
                return found
    return None


class ConfigRevisionRequest(BaseModel):
    active_model: str
    models: dict[str, dict[str, Any]]
    note: str = ""


class ConnectivityResult(BaseModel):
    ok: bool


class OverallRuntimeControlPlane:
    """Thin Overall control plane over existing Runtime/config/trace."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        state_root: str | Path,
        runtime_dbs: dict[str, str | Path] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.state_root = Path(state_root).resolve()
        self.revision_root = self.state_root / "revisions"
        self.audit_path = self.state_root / "audit.jsonl"
        self.active_path = self.state_root / "active.json"
        self.diagnostic_root = self.state_root / "diagnostic"
        self.canonical_model_config = (
            self.project_root / "config/runtime/model.yaml"
        ).resolve()
        self.binding_manifest = (
            self.project_root
            / "contracts/runtime_binding/v1/runtime_binding.json"
        ).resolve()
        self.runtime_dbs: dict[str, Path] = {
            name: Path(path).resolve()
            for name, path in (runtime_dbs or {}).items()
        }
        self.operator_model_config_overrides = {
            name: os.environ.get(name, "").strip()
            for name in _DOMAIN_MODEL_CONFIG_ENVS
            if os.environ.get(name, "").strip()
        }
        self.revision_root.mkdir(parents=True, exist_ok=True)
        self.diagnostic_root.mkdir(parents=True, exist_ok=True)

    def register_runtime_db(self, name: str, path: str | Path) -> None:
        self.runtime_dbs[str(name)] = Path(path).resolve()

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError("MODEL_CONFIG_ROOT_MUST_BE_MAPPING")
        return raw

    def _active_pointer(self) -> dict[str, Any]:
        if not self.active_path.is_file():
            return {
                "revision_id": "CANONICAL",
                "path": str(self.canonical_model_config),
            }
        try:
            raw = json.loads(self.active_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {
                "revision_id": "CANONICAL",
                "path": str(self.canonical_model_config),
            }
        path = Path(str(raw.get("path") or "")).resolve()
        revision_id = str(raw.get("revision_id") or "").strip()
        if revision_id == "CANONICAL":
            return {
                "revision_id": "CANONICAL",
                "path": str(self.canonical_model_config),
            }
        if (
            not revision_id
            or not path.is_file()
            or self.revision_root not in path.parents
        ):
            raise ValueError("ACTIVE_CONFIG_POINTER_INVALID")
        return raw

    def effective_model_config_path(self) -> Path:
        return Path(self._active_pointer()["path"]).resolve()

    def apply_active_config_environment(self) -> dict[str, Any]:
        path = self.effective_model_config_path()
        applied: dict[str, str] = {}
        preserved: dict[str, str] = {}
        for name in _DOMAIN_MODEL_CONFIG_ENVS:
            operator_value = self.operator_model_config_overrides.get(name)
            if operator_value:
                os.environ[name] = operator_value
                preserved[name] = operator_value
            else:
                os.environ[name] = str(path)
                applied[name] = str(path)
        os.environ.setdefault("OVERALL_RUNTIME_MODEL_CONFIG", str(path))
        return {
            "applied": applied,
            "preserved_operator_overrides": preserved,
        }

    def formal_bindings(self) -> dict[str, Any]:
        binding = load_runtime_binding(
            self.binding_manifest,
            repository_root=self.project_root,
        )
        items: list[dict[str, Any]] = []
        for domain in [
            *binding["domains"],
            *binding.get("shared_capabilities", []),
        ]:
            owner_id = (
                domain.get("domain_id") or domain.get("capability_id")
            )
            for agent_id, config_path in zip(
                domain["agent_ids"],
                domain["config_paths"],
            ):
                items.append(
                    {
                        "owner_id": owner_id,
                        "agent_id": agent_id,
                        "config_path": config_path,
                        "ownership": domain["ownership"],
                        "status": domain["status"],
                    }
                )
        return {
            "binding_contract_version": binding[
                "binding_contract_version"
            ],
            "runtime_contract_version": binding[
                "runtime_contract_version"
            ],
            "runtime_implementation_version": binding[
                "runtime_implementation_version"
            ],
            "agent_config_contract": binding["agent_config_contract"],
            "items": items,
            "boundary": binding["boundary"],
        }

    def effective_config(self) -> dict[str, Any]:
        pointer = self._active_pointer()
        path = Path(pointer["path"])
        raw = self._read_yaml(path)
        models = raw.get("models") or {}
        projected: dict[str, Any] = {}
        for model_ref, model_raw in models.items():
            model = dict(model_raw or {})
            api_key_env = (
                str(model.get("api_key_env") or "").strip() or None
            )
            base_url_env = (
                str(model.get("base_url_env") or "").strip() or None
            )
            projected[str(model_ref)] = {
                "provider": model.get("provider"),
                "model": model.get("model"),
                "base_url": model.get("base_url"),
                "base_url_env": base_url_env,
                "api_key_env": api_key_env,
                "api_key_present": bool(
                    api_key_env
                    and os.environ.get(api_key_env, "").strip()
                ),
                "max_tokens": model.get("max_tokens"),
                "temperature": model.get("temperature"),
                "metadata": _safe_value(model.get("metadata") or {}),
                "direct_secret_configured": bool(model.get("api_key")),
            }
        active_model = raw.get("active_model")
        return {
            "contract": "AGENT-CONFIG-001",
            "active_revision": pointer.get(
                "revision_id",
                "CANONICAL",
            ),
            "model_config_path": str(path),
            "model_config_hash": _sha256(raw),
            "active_model": active_model,
            "active_model_effective": projected.get(
                str(active_model)
            ) if active_model else None,
            "models": projected,
            "formal_bindings": self.formal_bindings(),
            "operator_overrides": {
                name: os.environ.get(name)
                for name in _DOMAIN_MODEL_CONFIG_ENVS
                if os.environ.get(name)
            },
            "secret_policy": "SECRET_REF_ONLY_FOR_CONTROL_PLANE_WRITES",
        }

    def _audit(
        self,
        *,
        action: str,
        revision_id: str,
        actor: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        record = {
            "observed_at": _now(),
            "action": action,
            "revision_id": revision_id,
            "actor": actor or "UNKNOWN",
            "details": _safe_value(details or {}),
        }
        with self.audit_path.open("a", encoding="utf-8") as stream:
            stream.write(_stable_json(record) + "\n")

    def list_revisions(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for path in sorted(
            self.revision_root.glob("*.json"),
            reverse=True,
        ):
            try:
                items.append(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (OSError, json.JSONDecodeError):
                continue
        audit: list[dict[str, Any]] = []
        if self.audit_path.is_file():
            for line in self.audit_path.read_text(
                encoding="utf-8"
            ).splitlines():
                try:
                    audit.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        pointer = self._active_pointer()
        return {
            "active_revision": pointer.get(
                "revision_id",
                "CANONICAL",
            ),
            "items": items,
            "audit": audit[-100:],
        }

    def create_revision(
        self,
        request: ConfigRevisionRequest,
        *,
        actor: str,
    ) -> dict[str, Any]:
        payload = request.model_dump(mode="json")
        candidate = {
            "active_model": payload["active_model"],
            "models": payload["models"],
        }
        if candidate["active_model"] not in candidate["models"]:
            raise ValueError("ACTIVE_MODEL_UNKNOWN")
        forbidden = _find_literal_secret(candidate)
        if forbidden:
            raise ValueError(
                f"LITERAL_SECRET_FORBIDDEN:{forbidden}"
            )
        try:
            ModelProfilesConfig.model_validate(candidate)
        except Exception as exc:
            raise ValueError(
                f"MODEL_CONFIG_INVALID:{exc}"
            ) from exc
        digest = _sha256(candidate)
        stamp = datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        revision_id = f"cfg-{stamp}-{digest[:10]}"
        yaml_path = self.revision_root / f"{revision_id}.yaml"
        meta_path = self.revision_root / f"{revision_id}.json"
        yaml_path.write_text(
            yaml.safe_dump(
                candidate,
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        meta = {
            "revision_id": revision_id,
            "created_at": _now(),
            "actor": actor or "UNKNOWN",
            "note": request.note,
            "config_hash": digest,
            "path": str(yaml_path),
        }
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._audit(
            action="CREATE_REVISION",
            revision_id=revision_id,
            actor=actor,
            details={
                "config_hash": digest,
                "note": request.note,
            },
        )
        return meta

    def activate_revision(
        self,
        revision_id: str,
        *,
        actor: str,
        action: str = "ACTIVATE",
    ) -> dict[str, Any]:
        previous = self._active_pointer().get(
            "revision_id",
            "CANONICAL",
        )
        if revision_id == "CANONICAL":
            target = self.canonical_model_config
        else:
            meta_path = (
                self.revision_root / f"{revision_id}.json"
            )
            if not meta_path.is_file():
                raise KeyError("CONFIG_REVISION_NOT_FOUND")
            meta = json.loads(
                meta_path.read_text(encoding="utf-8")
            )
            target = Path(meta["path"]).resolve()
            if (
                not target.is_file()
                or self.revision_root not in target.parents
            ):
                raise KeyError("CONFIG_REVISION_FILE_NOT_FOUND")
        pointer = {
            "revision_id": revision_id,
            "path": str(target),
            "activated_at": _now(),
            "actor": actor or "UNKNOWN",
            "previous_revision": previous,
            "apply_mode": "NEXT_RUNTIME_BUILD_OR_RESTART",
        }
        self.active_path.write_text(
            json.dumps(pointer, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._audit(
            action=action,
            revision_id=revision_id,
            actor=actor,
            details={"previous_revision": previous},
        )
        pointer["environment"] = self.apply_active_config_environment()
        return pointer

    def rollback(
        self,
        revision_id: str,
        *,
        actor: str,
    ) -> dict[str, Any]:
        return self.activate_revision(
            revision_id,
            actor=actor,
            action="ROLLBACK",
        )

    def binding_smoke(self, agent_id: str) -> dict[str, Any]:
        bindings = self.formal_bindings()
        item = next(
            (
                entry
                for entry in bindings["items"]
                if entry["agent_id"] == agent_id
            ),
            None,
        )
        if item is None:
            raise KeyError("FORMAL_AGENT_NOT_BOUND")
        agent_path = (
            self.project_root / item["config_path"]
        ).resolve()
        raw = self._read_yaml(agent_path)
        model_ref = raw.get("model_ref")
        effective = self.effective_config()
        active_model = (
            model_ref or effective.get("active_model")
        )
        model = (
            effective.get("models") or {}
        ).get(str(active_model))
        if model is None:
            return {
                "status": "BLOCKED",
                "agent_id": agent_id,
                "reason": "MODEL_REF_NOT_RESOLVABLE",
                "model_ref": active_model,
                "formal_binding": item,
            }
        secret_ready = (
            model.get("api_key_present")
            or not model.get("api_key_env")
        )
        endpoint_ref = model.get("base_url_env")
        endpoint_ready = bool(
            model.get("base_url")
            or (
                endpoint_ref
                and os.environ.get(str(endpoint_ref), "").strip()
            )
        )
        return {
            "status": (
                "PASS"
                if secret_ready and endpoint_ready
                else "BLOCKED"
            ),
            "agent_id": agent_id,
            "model_ref": active_model,
            "provider": model.get("provider"),
            "model": model.get("model"),
            "secret_ref": model.get("api_key_env"),
            "secret_present": bool(
                model.get("api_key_present")
            ),
            "endpoint_ref": endpoint_ref,
            "endpoint_present": endpoint_ready,
            "formal_binding": item,
            "execution_owner": "UNIFIED_RUNTIME",
            "silent_legacy_fallback": False,
        }

    def connectivity_test(self) -> dict[str, Any]:
        from runtime import (
            AgentConfigLoader,
            AgentRequest,
            ConfiguredAgentRuntime,
            RuntimeStatus,
            SqliteTaskStore,
        )

        effective = self.effective_config()
        active_model = str(
            effective.get("active_model") or ""
        ).strip()
        if not active_model:
            return {
                "status": "BLOCKED",
                "reason": "ACTIVE_MODEL_REQUIRED",
            }
        prompt_path = (
            self.diagnostic_root / "connectivity_prompt.md"
        )
        agent_path = (
            self.diagnostic_root / "connectivity_agent.yaml"
        )
        prompt_path.write_text(
            'Return exactly one JSON object: {"ok": true}.',
            encoding="utf-8",
        )
        agent_path.write_text(
            yaml.safe_dump(
                {
                    "agent_id": "overall.runtime.connectivity",
                    "model_ref": active_model,
                    "prompt": {"ref": str(prompt_path)},
                    "output_schema": {
                        "ref": "ConnectivityResult"
                    },
                    "execution": {
                        "retry": {
                            "transport_attempts": 1,
                            "validation_attempts": 1,
                            "step_attempts": 1,
                        },
                        "budget": {
                            "max_provider_calls_per_step": 1,
                            "max_provider_calls_per_task": 1,
                        },
                    },
                    "metadata": {
                        "provider_response_shape": "json_object",
                        "diagnostic_only": True,
                    },
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        try:
            loader = AgentConfigLoader(
                root=self.project_root,
                model_profiles=self.effective_model_config_path(),
                schemas={
                    "ConnectivityResult": ConnectivityResult
                },
                environ=os.environ,
            )
            runtime_db = self._connectivity_runtime_db()
            runtime = ConfiguredAgentRuntime(
                SqliteTaskStore(runtime_db),
                config_loader=loader,
            )
            resolved = runtime.load_agent(agent_path)
            result = runtime.invoke(
                AgentRequest(
                    request_id=(
                        "overall-connectivity-"
                        + hashlib.sha256(
                            _now().encode("utf-8")
                        ).hexdigest()[:16]
                    ),
                    agent_id=resolved.definition.agent_id,
                    input={"probe": True},
                    metadata={
                        "business_domain": "OVERALL_DIAGNOSTICS"
                    },
                )
            )
        except Exception as exc:
            return {
                "status": "FAIL",
                "reason": type(exc).__name__,
                "message": str(exc),
            }
        return {
            "status": (
                "PASS"
                if result.status == RuntimeStatus.COMPLETED
                else "FAIL"
            ),
            "task_id": result.task_id,
            "run_id": result.run_id,
            "provider": result.execution.provider,
            "model": result.execution.model,
            "provider_calls": result.execution.provider_calls,
            "error": (
                result.error.model_dump(mode="json")
                if result.error else None
            ),
            "runtime_owned": True,
            "probe_stack": "UNIFIED_RUNTIME",
            "runtime_db": str(runtime_db),
            "second_trace_store": False,
        }

    def _connectivity_runtime_db(self) -> Path:
        for name in (
            "QUALITY_ISSUE",
            "MAJOR_ISSUE",
            "STORAGE",
            "KNOWLEDGE",
            "HARDWARE_CASE",
        ):
            path = self.runtime_dbs.get(name)
            if path is not None:
                return path
        if self.runtime_dbs:
            return next(iter(self.runtime_dbs.values()))
        raise ValueError("EXISTING_RUNTIME_STORE_REQUIRED")

    @staticmethod
    def _table_exists(
        conn: sqlite3.Connection,
        table: str,
    ) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            is not None
        )

    def _read_runtime_db(
        self,
        name: str,
        path: Path,
        *,
        limit: int,
    ) -> dict[str, Any]:
        if not path.is_file():
            return {
                "name": name,
                "path": str(path),
                "status": "NOT_AVAILABLE",
                "tasks": [],
            }
        try:
            conn = sqlite3.connect(
                f"file:{path}?mode=ro",
                uri=True,
            )
            conn.row_factory = sqlite3.Row
        except sqlite3.Error as exc:
            return {
                "name": name,
                "path": str(path),
                "status": "ERROR",
                "error": str(exc),
                "tasks": [],
            }
        try:
            if not self._table_exists(
                conn,
                "runtime_task",
            ):
                return {
                    "name": name,
                    "path": str(path),
                    "status": "NOT_RUNTIME_DB",
                    "tasks": [],
                }
            rows = conn.execute(
                "SELECT task_id,request_id,status,record_json,"
                "error_json FROM runtime_task "
                "ORDER BY rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
            tasks: list[dict[str, Any]] = []
            for row in rows:
                record = json.loads(
                    row["record_json"] or "{}"
                )
                error = (
                    json.loads(row["error_json"])
                    if row["error_json"] else None
                )
                runs: list[dict[str, Any]] = []
                if self._table_exists(
                    conn,
                    "runtime_run",
                ):
                    run_rows = conn.execute(
                        "SELECT run_id,status,record_json "
                        "FROM runtime_run WHERE task_id=? "
                        "ORDER BY rowid DESC",
                        (row["task_id"],),
                    ).fetchall()
                    for run_row in run_rows:
                        run_record = json.loads(
                            run_row["record_json"] or "{}"
                        )
                        steps: list[dict[str, Any]] = []
                        if self._table_exists(
                            conn,
                            "runtime_step_run",
                        ):
                            step_rows = conn.execute(
                                "SELECT step_run_id,step_id,"
                                "status,record_json "
                                "FROM runtime_step_run "
                                "WHERE run_id=? ORDER BY rowid",
                                (run_row["run_id"],),
                            ).fetchall()
                            for step_row in step_rows:
                                step_record = json.loads(
                                    step_row["record_json"]
                                    or "{}"
                                )
                                attempts: list[
                                    dict[str, Any]
                                ] = []
                                if self._table_exists(
                                    conn,
                                    "runtime_attempt",
                                ):
                                    attempt_rows = conn.execute(
                                        "SELECT record_json "
                                        "FROM runtime_attempt "
                                        "WHERE step_run_id=? "
                                        "ORDER BY rowid",
                                        (
                                            step_row[
                                                "step_run_id"
                                            ],
                                        ),
                                    ).fetchall()
                                    attempts = [
                                        _safe_value(
                                            json.loads(
                                                item[
                                                    "record_json"
                                                ]
                                                or "{}"
                                            )
                                        )
                                        for item in attempt_rows
                                    ]
                                steps.append(
                                    {
                                        "step": _safe_value(
                                            step_record
                                        ),
                                        "attempts": attempts,
                                    }
                                )
                        runs.append(
                            {
                                "run": _safe_value(
                                    run_record
                                ),
                                "steps": steps,
                            }
                        )
                tasks.append(
                    {
                        "task": _safe_value(record),
                        "error": _safe_value(error),
                        "runs": runs,
                    }
                )
            return {
                "name": name,
                "path": str(path),
                "status": "READY",
                "tasks": tasks,
            }
        finally:
            conn.close()

    def diagnostics(
        self,
        *,
        limit: int = 20,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 100))
        stores: list[dict[str, Any]] = []
        seen_paths: set[Path] = set()
        for name, path in sorted(self.runtime_dbs.items()):
            resolved_path = path.resolve()
            if resolved_path in seen_paths:
                continue
            seen_paths.add(resolved_path)
            stores.append(
                self._read_runtime_db(
                    name,
                    resolved_path,
                    limit=safe_limit,
                )
            )
        ready = sum(
            item["status"] == "READY"
            for item in stores
        )
        return {
            "projection_version": (
                "overall-runtime-diagnostics/v1"
            ),
            "trace_owner": "UNIFIED_RUNTIME",
            "stores_ready": ready,
            "stores_total": len(stores),
            "stores": stores,
        }

    def knowledge_failures(
        self,
        *,
        limit: int = 20,
    ) -> dict[str, Any]:
        diagnostics = self.diagnostics(limit=limit)
        items: list[dict[str, Any]] = []
        for store in diagnostics["stores"]:
            for item in store.get("tasks") or []:
                task = item.get("task") or {}
                domain = str(
                    task.get("business_domain")
                    or task.get("metadata", {}).get(
                        "business_domain"
                    )
                    or ""
                )
                request_id = str(
                    task.get("request_id") or ""
                )
                if (
                    domain == "KNOWLEDGE_PRODUCTION"
                    or request_id.startswith(
                        "knowledge-extract:"
                    )
                ):
                    if (
                        str(task.get("status"))
                        != "COMPLETED"
                    ):
                        items.append(
                            {
                                "store": store["name"],
                                **item,
                            }
                        )
        return {
            "projection_version": (
                "overall-knowledge-runtime-failure/v1"
            ),
            "items": items,
        }


def create_overall_runtime_control_router(
    control: OverallRuntimeControlPlane,
    *,
    template_dir: str | Path | None = None,
) -> APIRouter:
    here = Path(__file__).resolve().parent
    templates = Jinja2Templates(
        directory=str(
            template_dir or (here / "templates")
        )
    )
    router = APIRouter()

    @router.get("/api/v2/system/agent-config")
    def agent_config() -> dict[str, Any]:
        return control.effective_config()

    @router.get(
        "/api/v2/system/agent-config/revisions"
    )
    def revisions() -> dict[str, Any]:
        return control.list_revisions()

    @router.post(
        "/api/v2/system/agent-config/revisions",
        status_code=201,
    )
    def create_revision(
        body: ConfigRevisionRequest,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return control.create_revision(
                body,
                actor=request.headers.get(
                    "X-Actor",
                    "UNKNOWN",
                ),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=str(exc),
            ) from exc

    @router.post(
        "/api/v2/system/agent-config/revisions/"
        "{revision_id}/activate"
    )
    def activate_revision(
        revision_id: str,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return control.activate_revision(
                revision_id,
                actor=request.headers.get(
                    "X-Actor",
                    "UNKNOWN",
                ),
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(
                status_code=404,
                detail=str(exc),
            ) from exc

    @router.post(
        "/api/v2/system/agent-config/rollback/"
        "{revision_id}"
    )
    def rollback_revision(
        revision_id: str,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return control.rollback(
                revision_id,
                actor=request.headers.get(
                    "X-Actor",
                    "UNKNOWN",
                ),
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(
                status_code=404,
                detail=str(exc),
            ) from exc

    @router.post(
        "/api/v2/system/agent-config/"
        "connectivity-test"
    )
    def connectivity_test() -> dict[str, Any]:
        return control.connectivity_test()

    @router.post(
        "/api/v2/system/agent-config/"
        "agent-smoke/{agent_id}"
    )
    def agent_smoke(
        agent_id: str,
    ) -> dict[str, Any]:
        try:
            return control.binding_smoke(agent_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail=str(exc),
            ) from exc

    @router.get(
        "/api/v2/system/runtime-diagnostics"
    )
    def runtime_diagnostics(
        limit: int = 20,
    ) -> dict[str, Any]:
        return control.diagnostics(limit=limit)

    @router.get(
        "/api/v2/system/runtime-diagnostics/knowledge"
    )
    def knowledge_diagnostics(
        limit: int = 20,
    ) -> dict[str, Any]:
        return control.knowledge_failures(limit=limit)

    @router.get(
        "/p0/system/agent-config",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def agent_config_page(
        request: Request,
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "overall_agent_config.html",
            {
                "page_title": "Agent 配置",
                "config": control.effective_config(),
                "revisions": control.list_revisions(),
            },
        )

    @router.get(
        "/p0/system/runtime-diagnostics",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def runtime_diagnostics_page(
        request: Request,
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "overall_runtime_diagnostics.html",
            {
                "page_title": "Runtime Diagnostics",
                "diagnostics": control.diagnostics(
                    limit=20
                ),
                "knowledge_failures": (
                    control.knowledge_failures(
                        limit=20
                    )
                ),
            },
        )

    return router


__all__ = [
    "ConfigRevisionRequest",
    "OverallRuntimeControlPlane",
    "create_overall_runtime_control_router",
]
