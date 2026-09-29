"""Overall R2 common Agent Config and Runtime Diagnostics control plane.

This module is intentionally a thin control plane over the existing Unified Runtime.
It does not own Provider HTTP, retry/checkpoint/resume, business repositories, or a
second trace store.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field

from runtime.binding import load_runtime_binding
from runtime.config import AgentConfigLoader
from runtime.config.models import ModelProfilesConfig
from runtime.contracts import AgentRequest, RuntimeStatus
from runtime.store import SqliteTaskStore


_HERE = Path(__file__).resolve().parent
FORMAL_BINDING_PATH = "contracts/runtime_binding/v1/runtime_binding.json"
CONNECTIVITY_AGENT_CONFIG = "config/runtime/storage.ai.json_call.yaml"

FORMAL_AGENT_CONFIGS: tuple[tuple[str, str], ...] = (
    ("major_issue.v2.occurrence", "config/runtime/agents/major_issue.v2.occurrence.yaml"),
    ("hardware_case.structure", "config/runtime/agents/hardware_case.structure.yaml"),
    ("reverse_quality.analysis", "config/runtime/agents/reverse_quality.analysis.yaml"),
    ("storage.emmc.parameter_extract", "config/runtime/agents/storage.emmc.parameter_extract.yaml"),
    ("knowledge.production.extract", "config/runtime/agents/knowledge.production.extract.yaml"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _safe_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_json(v) for v in value]
    return value


class RevisionProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    base_url_env: str | None = None
    api_key_env: str | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = None


class RevisionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = "OVERALL_OPERATOR"
    reason: str = "W5 Agent Config revision"
    active_model: str | None = None
    profile_patch: RevisionProfilePatch | None = None


class RevisionActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = "OVERALL_OPERATOR"


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_revision_id: str
    actor: str = "OVERALL_OPERATOR"
    reason: str = "ROLLBACK"


class RuntimeControlPlane:
    """Govern existing Runtime config revisions and project existing Runtime traces."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        control_root: str | Path,
        runtime_db_path: str | Path,
        base_model_config: str | Path | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.control_root = Path(control_root).resolve()
        self.runtime_db_path = Path(runtime_db_path).resolve()
        self.revision_root = self.control_root / "revisions"
        self.active_pointer_path = self.control_root / "active.json"
        self.audit_path = self.control_root / "audit.jsonl"
        self.base_model_config = Path(
            base_model_config
            or self.project_root / "config" / "runtime" / "model.yaml"
        ).expanduser().resolve()
        self.revision_root.mkdir(parents=True, exist_ok=True)

    def _read_yaml(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise ValueError(f"MODEL_CONFIG_NOT_FOUND:{path}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError("MODEL_CONFIG_ROOT_INVALID")
        ModelProfilesConfig.model_validate(raw)
        return raw

    @staticmethod
    def _validate_plaintext_secret_policy(raw: dict[str, Any]) -> None:
        for profile_id, profile in dict(raw.get("models") or {}).items():
            if not isinstance(profile, dict):
                continue
            direct = profile.get("api_key")
            if direct is None:
                continue
            value = str(direct)
            if value.startswith("__REPLACE_") or value in {"", "__TEST_ONLY__"}:
                continue
            raise ValueError(f"PLAINTEXT_SECRET_FORBIDDEN:{profile_id}")

    def _safe_profile(self, profile_id: str, profile: dict[str, Any]) -> dict[str, Any]:
        api_key_env = str(profile.get("api_key_env") or "").strip() or None
        direct = profile.get("api_key")
        secret_ref = api_key_env
        secret_mode = "ENV_REF" if api_key_env else "NONE"
        secret_present = bool(os.environ.get(api_key_env, "")) if api_key_env else False
        if direct is not None:
            secret_ref = "DIRECT_TEST_PLACEHOLDER"
            secret_mode = "DIRECT_TEST_ONLY"
            secret_present = False
        return {
            "profile_id": profile_id,
            "provider": profile.get("provider"),
            "model": profile.get("model"),
            "base_url": profile.get("base_url"),
            "base_url_env": profile.get("base_url_env"),
            "secret_ref": secret_ref,
            "secret_mode": secret_mode,
            "secret_present": secret_present,
            "max_tokens": profile.get("max_tokens"),
            "temperature": profile.get("temperature"),
            "metadata": _safe_json(profile.get("metadata") or {}),
        }

    def _pointer(self) -> dict[str, Any] | None:
        if not self.active_pointer_path.is_file():
            return None
        try:
            value = json.loads(self.active_pointer_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("ACTIVE_CONFIG_POINTER_INVALID") from exc
        if not isinstance(value, dict):
            raise ValueError("ACTIVE_CONFIG_POINTER_INVALID")
        return value

    def active_config_path(self) -> Path:
        pointer = self._pointer()
        if pointer is None:
            return self.base_model_config
        revision_id = str(pointer.get("revision_id") or "").strip()
        expected = (self.revision_root / revision_id / "model.yaml").resolve()
        if self.revision_root not in expected.parents or not expected.is_file():
            raise ValueError("ACTIVE_CONFIG_POINTER_OUTSIDE_CONTROL_ROOT")
        return expected

    def _append_audit(self, event: dict[str, Any]) -> None:
        self.control_root.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    def revisions(self) -> list[dict[str, Any]]:
        active = (self._pointer() or {}).get("revision_id")
        items: list[dict[str, Any]] = []
        for metadata_path in sorted(self.revision_root.glob("*/metadata.json"), reverse=True):
            try:
                item = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(item, dict):
                continue
            item["active"] = item.get("revision_id") == active
            items.append(item)
        return items

    def audit_events(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.audit_path.is_file():
            return []
        lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        events: list[dict[str, Any]] = []
        for line in lines[-max(1, min(limit, 200)):]:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                events.append(value)
        return events

    def effective_config(self) -> dict[str, Any]:
        path = self.active_config_path()
        raw = self._read_yaml(path)
        self._validate_plaintext_secret_policy(raw)
        models = dict(raw.get("models") or {})
        active_model = str(raw.get("active_model") or "").strip() or None
        pointer = self._pointer()
        profiles = [
            self._safe_profile(profile_id, dict(profile or {}))
            for profile_id, profile in models.items()
        ]
        formal_binding = load_runtime_binding(
            self.project_root / FORMAL_BINDING_PATH,
            repository_root=self.project_root,
        )
        return {
            "control_plane_version": "overall-agent-config-control/v1",
            "source": "CONTROL_REVISION" if pointer else "BASELINE_FILE",
            "config_path": str(path),
            "config_sha256": _sha256_bytes(path.read_bytes()),
            "active_revision_id": (pointer or {}).get("revision_id"),
            "active_model": active_model,
            "profiles": profiles,
            "formal_binding": {
                "contract_version": formal_binding["binding_contract_version"],
                "status": formal_binding["status"],
                "boundary": formal_binding["boundary"],
                "formal_agent_ids": [
                    agent_id
                    for domain in [
                        *formal_binding["domains"],
                        *formal_binding.get("shared_capabilities", []),
                    ]
                    for agent_id in domain["agent_ids"]
                ],
            },
            "revision_count": len(self.revisions()),
            "restart_required_after_activation": True,
            "plaintext_secret_exposure": False,
        }

    def create_revision(self, request: RevisionCreateRequest) -> dict[str, Any]:
        source = self.active_config_path()
        raw = self._read_yaml(source)
        self._validate_plaintext_secret_policy(raw)
        updated = json.loads(json.dumps(raw))

        if request.active_model is not None:
            active_model = request.active_model.strip()
            if active_model not in dict(updated.get("models") or {}):
                raise ValueError("ACTIVE_MODEL_UNKNOWN")
            updated["active_model"] = active_model

        patch = request.profile_patch
        if patch is not None:
            profile_id = patch.profile_id.strip()
            models = dict(updated.get("models") or {})
            if profile_id not in models:
                raise ValueError("MODEL_PROFILE_UNKNOWN")
            profile = dict(models[profile_id] or {})
            for key in ("provider", "model", "max_tokens", "temperature"):
                value = getattr(patch, key)
                if value is not None:
                    profile[key] = value
            if patch.base_url is not None:
                profile["base_url"] = patch.base_url.strip()
                profile.pop("base_url_env", None)
            if patch.base_url_env is not None:
                profile["base_url_env"] = patch.base_url_env.strip()
                profile.pop("base_url", None)
            if patch.api_key_env is not None:
                profile["api_key_env"] = patch.api_key_env.strip()
                profile.pop("api_key", None)
            models[profile_id] = profile
            updated["models"] = models

        ModelProfilesConfig.model_validate(updated)
        self._validate_plaintext_secret_policy(updated)
        serialized = yaml.safe_dump(updated, allow_unicode=True, sort_keys=False)
        digest = _sha256_bytes(serialized.encode("utf-8"))
        revision_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + digest[:10]
        target_dir = self.revision_root / revision_id
        target_dir.mkdir(parents=True, exist_ok=False)
        config_path = target_dir / "model.yaml"
        config_path.write_text(serialized, encoding="utf-8")
        metadata = {
            "revision_id": revision_id,
            "created_at": _now(),
            "actor": request.actor.strip() or "OVERALL_OPERATOR",
            "reason": request.reason.strip(),
            "source_config_sha256": _sha256_bytes(source.read_bytes()),
            "config_sha256": digest,
            "config_path": str(config_path.resolve()),
        }
        (target_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._append_audit({"event": "REVISION_CREATED", **metadata})
        return metadata

    def activate_revision(self, revision_id: str, *, actor: str, event: str = "ACTIVATED", reason: str = "") -> dict[str, Any]:
        target = (self.revision_root / revision_id / "model.yaml").resolve()
        if self.revision_root not in target.parents or not target.is_file():
            raise ValueError("REVISION_NOT_FOUND")
        raw = self._read_yaml(target)
        self._validate_plaintext_secret_policy(raw)
        pointer = {
            "revision_id": revision_id,
            "config_path": str(target),
            "activated_at": _now(),
            "actor": actor.strip() or "OVERALL_OPERATOR",
        }
        self.control_root.mkdir(parents=True, exist_ok=True)
        temp = self.active_pointer_path.with_suffix(".next")
        temp.write_text(json.dumps(pointer, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.active_pointer_path)
        self._append_audit(
            {
                "event": event,
                **pointer,
                "reason": reason,
                "restart_required": True,
            }
        )
        # Existing Runtime instances keep immutable execution snapshots. New Runtime
        # instances in this process resolve the activated config through these
        # already-supported environment entry points.
        for name in ("MAJOR_MODEL_CONFIG", "STORAGE_MODEL_CONFIG", "HARDWARE_CASE_MODEL_CONFIG"):
            os.environ[name] = str(target)
        os.environ["STORAGE_MODEL_CONFIG_SOURCE"] = "OVERALL_AGENT_CONFIG"
        return {
            **pointer,
            "status": "ACTIVE",
            "restart_required": True,
            "effective_on": "NEXT_RUNTIME_BUILD_OR_PROCESS_RESTART",
        }

    def rollback(self, request: RollbackRequest) -> dict[str, Any]:
        return self.activate_revision(
            request.target_revision_id,
            actor=request.actor,
            event="ROLLED_BACK",
            reason=request.reason,
        )

    def _structural_environ(self, config_path: Path) -> dict[str, str]:
        raw = self._read_yaml(config_path)
        active = str(raw.get("active_model") or "")
        profile = dict((raw.get("models") or {}).get(active) or {})
        env = dict(os.environ)
        base_env = str(profile.get("base_url_env") or "").strip()
        key_env = str(profile.get("api_key_env") or "").strip()
        if base_env and not env.get(base_env):
            env[base_env] = "http://provider.invalid/v1"
        if key_env and not env.get(key_env):
            env[key_env] = "W5_STRUCTURAL_SMOKE_ONLY"
        return env

    @staticmethod
    def _schema_registry() -> dict[str, Any]:
        from knowledge_production.models import KnowledgeExtractionOutput
        from quality_knowledge.models.analysis_v2 import OccurrenceAnalysisV2DTO
        from runtime.adapters import StorageFieldResult
        from services.hardware_case_runtime_adapter import HARDWARE_CASE_STRUCTURE_SCHEMA

        return {
            "OccurrenceAnalysisV2DTO": OccurrenceAnalysisV2DTO,
            "HardwareCaseStructureOutput": HARDWARE_CASE_STRUCTURE_SCHEMA,
            "StorageFieldResult": StorageFieldResult,
            "KnowledgeExtractionOutput": KnowledgeExtractionOutput,
        }

    def formal_agent_smoke(self) -> dict[str, Any]:
        config_path = self.active_config_path()
        binding = load_runtime_binding(
            self.project_root / FORMAL_BINDING_PATH,
            repository_root=self.project_root,
        )
        environ = self._structural_environ(config_path)
        loader = AgentConfigLoader(
            root=self.project_root,
            model_profiles=config_path,
            schemas=self._schema_registry(),
            content_strategies={
                "storage_linked_fields@1": {
                    "version": "1",
                    "kind": "storage_linked_fields",
                }
            },
            completeness_gates={
                "storage_parameter_gate": {
                    "version": "v1",
                    "kind": "storage_parameter_gate",
                }
            },
            environ=environ,
        )
        items: list[dict[str, Any]] = []
        expected = {
            agent_id: config_rel
            for agent_id, config_rel in FORMAL_AGENT_CONFIGS
        }
        bound = {
            agent_id: config_rel
            for domain in [
                *binding["domains"],
                *binding.get("shared_capabilities", []),
            ]
            for agent_id, config_rel in zip(domain["agent_ids"], domain["config_paths"])
        }
        if bound != expected:
            raise ValueError("FORMAL_AGENT_BINDING_DRIFT")
        for agent_id, config_rel in FORMAL_AGENT_CONFIGS:
            resolved = loader.load(self.project_root / config_rel)
            if resolved.definition.agent_id != agent_id:
                raise ValueError("FORMAL_AGENT_ID_MISMATCH")
            items.append(
                {
                    "agent_id": agent_id,
                    "config_path": config_rel,
                    "config_hash": resolved.config_hash,
                    "config_version": resolved.config_version,
                    "provider": resolved.provider.type,
                    "model_ref": resolved.provider.profile_ref,
                    "model": resolved.provider.model,
                    "runtime_enforced": True,
                    "silent_legacy_fallback": False,
                }
            )
        return {
            "status": "PASS",
            "smoke_type": "FORMAL_AGENT_CONFIG_REGISTRATION",
            "formal_agent_count": len(items),
            "items": items,
            "runtime_binding_status": binding["status"],
            "formal_agent_unified_runtime_percent": 100,
            "silent_legacy_fallback_count": 0,
        }

    def connectivity_test(self) -> dict[str, Any]:
        """Execute one harmless structured call through the existing Runtime Provider stack."""
        product_root = self.project_root / "products" / "storage_rc1"
        config_path = self.active_config_path()
        loader = AgentConfigLoader(
            root=product_root,
            model_profiles=config_path,
            schemas={"StorageDynamicJson": {"type": "object"}},
            environ=os.environ,
        )
        runtime = __import__("runtime", fromlist=["ConfiguredAgentRuntime"]).ConfiguredAgentRuntime(
            SqliteTaskStore(self.runtime_db_path),
            config_loader=loader,
        )
        resolved = runtime.load_agent(product_root / CONNECTIVITY_AGENT_CONFIG)
        request = AgentRequest(
            request_id="overall-w5-connectivity-" + uuid4().hex,
            agent_id=resolved.definition.agent_id,
            input={
                "instructions": "Return only one JSON object with ok=true.",
                "provider_payload": {"probe": "OVERALL_R2_W5_CONNECTIVITY"},
                "schema": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                    "additionalProperties": True,
                },
            },
            metadata={"business_domain": "OVERALL_CONTROL_PLANE", "probe": "CONNECTIVITY"},
        )
        result = runtime.invoke(request)
        error = result.error.model_dump(mode="json") if result.error is not None else None
        return {
            "status": "PASS" if result.status == RuntimeStatus.COMPLETED else "FAIL",
            "runtime_status": result.status.value,
            "task_id": result.task_id,
            "run_id": result.run_id,
            "agent_id": resolved.definition.agent_id,
            "config_hash": resolved.config_hash,
            "provider": resolved.provider.type,
            "model_ref": resolved.provider.profile_ref,
            "model": resolved.provider.model,
            "provider_calls": result.execution.provider_calls,
            "last_error": error,
            "provider_stack": "UNIFIED_RUNTIME",
            "second_probe_stack": False,
        }

    @staticmethod
    def _read_record_rows(conn: sqlite3.Connection, table: str, limit: int) -> list[dict[str, Any]]:
        rows = conn.execute(
            f"SELECT record_json FROM {table} ORDER BY rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        values: list[dict[str, Any]] = []
        for row in rows:
            try:
                value = json.loads(row[0])
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                values.append(value)
        return values

    def _diagnostic_store(self, path: Path, *, label: str, limit: int) -> dict[str, Any]:
        if not path.is_file():
            return {"label": label, "path": str(path), "status": "NOT_AVAILABLE"}
        try:
            conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            required = {"runtime_task", "runtime_run", "runtime_step_run", "runtime_attempt"}
            if not required.issubset(tables):
                return {"label": label, "path": str(path), "status": "NOT_RUNTIME_STORE"}
            tasks = self._read_record_rows(conn, "runtime_task", limit)
            runs = self._read_record_rows(conn, "runtime_run", limit)
            steps = self._read_record_rows(conn, "runtime_step_run", limit * 2)
            attempts = self._read_record_rows(conn, "runtime_attempt", limit * 4)
        except sqlite3.Error as exc:
            return {
                "label": label,
                "path": str(path),
                "status": "UNAVAILABLE",
                "error": type(exc).__name__,
            }
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return {
            "label": label,
            "path": str(path),
            "status": "READY",
            "tasks": tasks,
            "runs": runs,
            "steps": steps,
            "attempts": attempts,
        }

    def diagnostic_paths(self, app_state: Any) -> list[tuple[str, Path]]:
        candidates: list[tuple[str, Path]] = [("QUALITY_ISSUE", self.runtime_db_path)]
        major = getattr(app_state, "major_case_production_service", None)
        major_store = getattr(major, "store", None)
        major_path = getattr(major_store, "db_path", None)
        if major_path:
            candidates.append(("MAJOR_CASE", Path(major_path).resolve()))
        stage_runner = getattr(app_state, "v2_stage_runner", None)
        stage_store = getattr(getattr(stage_runner, "runtime", None), "store", None)
        stage_path = getattr(stage_store, "db_path", None)
        if stage_path:
            candidates.append(("QUALITY_ISSUE", Path(stage_path).resolve()))
        storage_raw = os.environ.get("STORAGE_LIFE_RUNTIME_DB", "").strip()
        if storage_raw:
            candidates.append(("STORAGE", Path(storage_raw).expanduser().resolve()))
        hardware_raw = os.environ.get("HARDWARE_CASE_RUNTIME_DB", "").strip()
        if hardware_raw:
            candidates.append(("HARDWARE_CASE", Path(hardware_raw).expanduser().resolve()))
        knowledge_root = os.environ.get("STORAGE_KNOWLEDGE_REPOSITORY_DIR", "").strip()
        if knowledge_root:
            candidates.append(
                (
                    "KNOWLEDGE_PRODUCTION",
                    Path(knowledge_root).expanduser().resolve()
                    / "runtime"
                    / "knowledge_production_runtime.sqlite3",
                )
            )
        dedup: list[tuple[str, Path]] = []
        seen: set[Path] = set()
        for label, path in candidates:
            if path in seen:
                continue
            seen.add(path)
            dedup.append((label, path))
        return dedup

    def diagnostics(self, app_state: Any, *, limit: int = 20) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 100))
        stores = [
            self._diagnostic_store(path, label=label, limit=safe_limit)
            for label, path in self.diagnostic_paths(app_state)
        ]
        failed_steps: list[dict[str, Any]] = []
        knowledge_failures: list[dict[str, Any]] = []
        for store in stores:
            if store.get("status") != "READY":
                continue
            for step in store.get("steps") or []:
                status = str(step.get("status") or "").upper()
                if status == "FAILED":
                    item = {
                        "store": store["label"],
                        "step_run_id": step.get("step_run_id"),
                        "run_id": step.get("run_id"),
                        "step_id": step.get("step_id"),
                        "agent_id": step.get("agent_id"),
                        "status": status,
                        "attempt_count": step.get("attempt_count"),
                        "error": step.get("error"),
                    }
                    failed_steps.append(item)
                    if store["label"] == "KNOWLEDGE_PRODUCTION":
                        knowledge_failures.append(item)
        return {
            "diagnostics_version": "overall-runtime-diagnostics/v1",
            "source": "EXISTING_RUNTIME_STORES_READ_ONLY",
            "store_count": len(stores),
            "stores": stores,
            "failed_steps": failed_steps,
            "knowledge_production_failures": knowledge_failures,
            "second_trace_store": False,
        }


def create_runtime_control_plane_router(
    control_plane: RuntimeControlPlane,
    *,
    template_dir: str | Path | None = None,
) -> APIRouter:
    templates = Jinja2Templates(directory=str(template_dir or (_HERE / "templates")))
    router = APIRouter()

    @router.get("/api/v2/overall/agent-config")
    def agent_config() -> dict[str, Any]:
        try:
            return control_plane.effective_config()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/api/v2/overall/agent-config/revisions")
    def config_revisions() -> dict[str, Any]:
        return {
            "items": control_plane.revisions(),
            "audit": control_plane.audit_events(),
        }

    @router.post("/api/v2/overall/agent-config/revisions", status_code=201)
    def create_revision(body: RevisionCreateRequest) -> dict[str, Any]:
        try:
            return control_plane.create_revision(body)
        except (ValueError, FileExistsError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/api/v2/overall/agent-config/revisions/{revision_id}/activate")
    def activate_revision(revision_id: str, body: RevisionActivateRequest) -> dict[str, Any]:
        try:
            return control_plane.activate_revision(revision_id, actor=body.actor)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post("/api/v2/overall/agent-config/rollback")
    def rollback(body: RollbackRequest) -> dict[str, Any]:
        try:
            return control_plane.rollback(body)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post("/api/v2/overall/agent-config/agent-smoke")
    def agent_smoke() -> dict[str, Any]:
        try:
            return control_plane.formal_agent_smoke()
        except Exception as exc:
            raise HTTPException(409, f"{type(exc).__name__}:{exc}") from exc

    @router.post("/api/v2/overall/agent-config/connectivity-test")
    def connectivity_test() -> dict[str, Any]:
        try:
            result = control_plane.connectivity_test()
        except Exception as exc:
            raise HTTPException(503, f"{type(exc).__name__}:{exc}") from exc
        if result["status"] != "PASS":
            raise HTTPException(503, result)
        return result

    @router.get("/api/v2/overall/runtime-diagnostics")
    def runtime_diagnostics(request: Request, limit: int = 20) -> dict[str, Any]:
        return control_plane.diagnostics(request.app.state, limit=limit)

    @router.get("/p0/system/agent-config", response_class=HTMLResponse, include_in_schema=False)
    def agent_config_page(request: Request) -> HTMLResponse:
        try:
            effective = control_plane.effective_config()
            error = None
        except Exception as exc:
            effective = {}
            error = f"{type(exc).__name__}:{exc}"
        return templates.TemplateResponse(
            request,
            "overall_agent_config.html",
            {
                "page_title": "Agent 配置",
                "effective": effective,
                "revisions": control_plane.revisions(),
                "audit": control_plane.audit_events(),
                "config_error": error,
            },
        )

    @router.get("/p0/system/runtime-diagnostics", response_class=HTMLResponse, include_in_schema=False)
    def runtime_diagnostics_page(request: Request) -> HTMLResponse:
        diagnostics = control_plane.diagnostics(request.app.state, limit=20)
        return templates.TemplateResponse(
            request,
            "overall_runtime_diagnostics.html",
            {
                "page_title": "Runtime Diagnostics",
                "diagnostics": diagnostics,
            },
        )

    return router


__all__ = [
    "RuntimeControlPlane",
    "RevisionCreateRequest",
    "RevisionActivateRequest",
    "RollbackRequest",
    "create_runtime_control_plane_router",
]
