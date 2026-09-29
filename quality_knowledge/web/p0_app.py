"""FastAPI composition root for platform and product-scoped profiles."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from quality_knowledge.web.hardware_case_api import create_hardware_case_router
from quality_knowledge.web.hardware_tree_import_api import create_hardware_tree_import_router
from quality_knowledge.web.p0_pages import (
    create_hardware_case_pages_router,
    create_p0_insights_router,
)
from quality_knowledge.p04.adapter import P04Provider, UnavailableP04Provider
from quality_knowledge.p04.api import create_p04_router, create_public_scenario_router
from quality_knowledge.p04.portrait import (
    PortraitArchiveRepository,
    PortraitProvider,
    PortraitService,
    UnavailablePortraitProvider,
)
from quality_knowledge.p04.portrait_api import create_portrait_router
from quality_knowledge.p04.service import P04InsightService
from quality_knowledge.major_cases.context import UnavailableMajorProblemContextProvider
from quality_knowledge.web.major_context_api import create_major_context_router
from repositories.hardware_case_repository import HardwareCaseRepository
from repositories.hardware_tree_import_repository import HardwareTreeImportRepository
from services.hardware_case_backend import HardwareCaseBackendService
from services.hardware_case_intake import HardwareCaseIntakeService
from services.hardware_case_source_store import HardwareCaseSourceStore
from services.hardware_tree_import_files import HardwareTreeImportFileStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORAGE_WORKSPACE_PREFIX = "/storage-workspace"
FULL_DOMAINS = frozenset({"QUALITY_ISSUE", "REPEAT_RISK", "HARDWARE_CASE"})
KNOWN_DOMAINS = FULL_DOMAINS


def _normalize_hardware_case_host_role(value: str | None) -> str:
    role = str(value or "CONSUMER").strip().upper()
    if role not in {"CONSUMER", "MAINTAINER"}:
        raise ValueError("HARDWARE_CASE_HOST_ROLE_INVALID")
    return role


def _normalize_domains(
    enabled_domains: set[str] | frozenset[str] | None,
) -> frozenset[str]:
    if enabled_domains is None:
        return FULL_DOMAINS
    domains = frozenset(str(item).strip().upper() for item in enabled_domains)
    unknown = domains - KNOWN_DOMAINS
    if unknown:
        raise ValueError("UNKNOWN_DOMAIN:" + ",".join(sorted(unknown)))
    if "REPEAT_RISK" in domains and "QUALITY_ISSUE" not in domains:
        raise ValueError("REPEAT_RISK_REQUIRES_QUALITY_ISSUE")
    if not domains:
        raise ValueError("AT_LEAST_ONE_DOMAIN_REQUIRED")
    return domains


def create_p0_app(
    db_path: str | Path,
    *,
    stage_runner: Any | None = None,
    project_root: str | Path = PROJECT_ROOT,
    runtime_model_config: str | Path | None = None,
    hardware_case_db_path: str | Path | None = None,
    hardware_tree_upload_dir: str | Path | None = None,
    hardware_case_source_root: str | Path | None = None,
    hardware_case_structurer: Any | None = None,
    hardware_case_host_role: str | None = None,
    repeat_web: Any | None = None,
    p04_provider: P04Provider | None = None,
    portrait_provider: PortraitProvider | None = None,
    portrait_db_path: str | Path | None = None,
    major_context_provider: Any | None = None,
    major_case_db_path: str | Path | None = None,
    major_attachment_root: str | Path | None = None,
    major_artifact_root: str | Path | None = None,
    major_provider: Any | None = None,
    overall_task_provider: Any | None = None,
    enabled_domains: set[str] | frozenset[str] | None = None,
    storage_app: Any | None = None,
    storage_workspace_prefix: str = DEFAULT_STORAGE_WORKSPACE_PREFIX,
    testability_enabled: bool | None = None,
    testability_token: str | None = None,
    testability_state_root: str | Path | None = None,
    legacy_quality_issue_db_path: str | Path | None = None,
) -> FastAPI:
    """Build the shared Web host with explicit domain composition.

    The default remains the historical full platform.  Product test packages
    may request only HARDWARE_CASE; that profile reuses the same FastAPI host,
    /api/v2 surface, templates and port without initializing unrelated domains.
    """
    domains = _normalize_domains(enabled_domains)
    root = Path(project_root)
    primary_db = Path(db_path)
    testability_mutable_paths: list[Path] = [primary_db]
    testability_restore_hooks: list[Any] = []
    app = FastAPI(title="Quality Capability P1", version="2.1.0")
    app.state.enabled_domains = tuple(sorted(domains))
    app.state.overall_shell_enabled = domains == FULL_DOMAINS
    app.state.hardware_case_host_role = _normalize_hardware_case_host_role(
        hardware_case_host_role
        if hardware_case_host_role is not None
        else os.getenv("HARDWARE_CASE_HOST_ROLE", "CONSUMER")
    )
    app.state.storage_workspace_binding = None
    if storage_app is not None or app.state.overall_shell_enabled:
        from quality_knowledge.web.storage_workspace import bind_storage_workspace

        app.state.storage_workspace_binding = bind_storage_workspace(
            app,
            storage_app=storage_app,
            prefix=storage_workspace_prefix,
        )

    repository: Any | None = None
    analysis_runtime_status: dict[str, Any] = {
        "ready": False,
        "source": "DOMAIN_DISABLED",
        "errors": [],
    }
    status: dict[str, Any] = {
        "outcome": "READY",
        "initialization_state": "NOT_REQUIRED",
        "enabled_domains": sorted(domains),
    }

    if "QUALITY_ISSUE" in domains:
        # Lazy imports keep Hardware Case-only startup/package independent from
        # Quality Issue and Repeat Risk implementation modules.
        from quality_knowledge.model_config import (
            ModelConfigError,
            load_quality_issue_ai_config,
            validate_quality_issue_ai_config,
        )
        from quality_knowledge.p0.initializer import P0InitializationError, P0Initializer
        from quality_knowledge.p0.repository import P0Repository
        from quality_knowledge.p0.stage_runner import (
            ProductionV2StageRunner,
            RuntimeConfiguredV2StageRunner,
        )
        from runtime.config import AgentConfigError

        initializer = P0Initializer(
            manifest_path=root / "quality_knowledge/config/p0_seed_manifest.json",
            plc_seed_path=root / "quality_knowledge/config/plc_fields.yaml",
        )
        try:
            status = initializer.verify_ready(db_path)
        except P0InitializationError as error:
            status = {
                "outcome": "INITIALIZATION_BLOCKED",
                "initialization_state": "INITIALIZATION_BLOCKED",
                "error": error.code,
                "diagnostic": error.diagnostic.as_dict(),
            }

            @app.get("/api/v2/initialization/status", status_code=503)
            def blocked_status() -> dict[str, Any]:
                return status

            app.state.initialization_status = status
            return app

        repository = P0Repository(db_path)
        if stage_runner is None:
            try:
                diagnostic = validate_quality_issue_ai_config(root, require_enabled=True)
                if diagnostic["ok"]:
                    ai_config, _ = load_quality_issue_ai_config(root)
                    fallback_runner = ProductionV2StageRunner(
                        repository,
                        ai_config=ai_config,
                    )
                    runtime_db_path = Path(db_path).with_name(
                        Path(db_path).name + ".runtime.db"
                    )
                    stage_runner = RuntimeConfiguredV2StageRunner.from_project(
                        repository,
                        root=root,
                        runtime_db_path=runtime_db_path,
                        fallback_runner=fallback_runner,
                        model_config_path=runtime_model_config,
                    )
                    testability_mutable_paths.append(runtime_db_path)
                    runtime_store = getattr(
                        getattr(stage_runner, "runtime", None), "store", None
                    )
                    ensure_runtime_schema = getattr(runtime_store, "_init_schema", None)
                    if callable(ensure_runtime_schema):
                        testability_restore_hooks.append(ensure_runtime_schema)
                analysis_runtime_status = {
                    **diagnostic,
                    "ready": stage_runner is not None,
                    "source": "UNIFIED_RUNTIME+MODEL_CONFIG",
                    "migrated_agent": RuntimeConfiguredV2StageRunner.AGENT_ID,
                    "model_config_path": (
                        str(stage_runner.model_config_path)
                        if stage_runner is not None
                        and hasattr(stage_runner, "model_config_path")
                        else None
                    ),
                }
            except (ModelConfigError, AgentConfigError, ValueError) as error:
                stage_runner = None
                analysis_runtime_status = {
                    "ready": False,
                    "source": "UNIFIED_RUNTIME+MODEL_CONFIG",
                    "migrated_agent": RuntimeConfiguredV2StageRunner.AGENT_ID,
                    "errors": [str(error)],
                }
        else:
            analysis_runtime_status = {
                "ready": True,
                "source": "INJECTED_RUNNER",
                "errors": [],
            }

    app.state.initialization_status = status
    app.state.p0_repository = repository
    app.state.v2_stage_runner = stage_runner
    app.state.analysis_runtime_status = analysis_runtime_status
    app.state.legacy_quality_issue_status = {
        "ready": False,
        "code": "DOMAIN_DISABLED" if "QUALITY_ISSUE" not in domains else "LEGACY_DB_PATH_NOT_CONFIGURED",
    }
    app.state.legacy_scenario_status = {
        "ready": False,
        "code": "DOMAIN_DISABLED" if "QUALITY_ISSUE" not in domains else "LEGACY_DB_PATH_NOT_CONFIGURED",
        "mode": "READ_ONLY",
    }
    # P04 is intentionally provider-injected.  The default is explicit
    # DATA_UNAVAILABLE until the approved public JSON providers are wired.
    app.state.p04_provider = p04_provider or UnavailableP04Provider()
    app.state.p04_service = P04InsightService(app.state.p04_provider)
    app.state.portrait_provider = portrait_provider or UnavailablePortraitProvider()
    portrait_db = (
        Path(portrait_db_path)
        if portrait_db_path is not None
        else primary_db.with_name(primary_db.stem + ".p04-portrait.db")
    )
    testability_mutable_paths.append(portrait_db)
    app.state.portrait_repository = PortraitArchiveRepository(portrait_db)
    app.state.portrait_service = PortraitService(
        app.state.portrait_provider,
        app.state.portrait_repository,
    )
    app.state.major_context_provider = (
        major_context_provider or UnavailableMajorProblemContextProvider()
    )
    # The provider is injected at the composition boundary.  P04 can only see
    # this HTTP JSON route and never imports the provider's repository/domain.
    app.include_router(create_major_context_router(app.state.major_context_provider))

    # Major production owns its SQLite store; downstream domains receive only
    # historical-case/v1 over the published artifact repository.
    major_case_service: Any | None = None
    historical_case_service: Any | None = None
    if "QUALITY_ISSUE" in domains:
        from quality_knowledge.major_cases.repository import MajorKnowledgeRepository
        from quality_knowledge.major_cases.restore import MajorCaseRestoreService
        from quality_knowledge.web.major_production_api import create_major_production_router
        from repositories import JsonArtifactRepository
        from services.historical_case_contract import HistoricalCaseConsumerService
        from services.major_case_production import MajorCaseProductionService
        from services.major_case_retrieval import MajorPublishedCaseSearchAdapter

        major_db = (
            Path(major_case_db_path)
            if major_case_db_path is not None
            else Path(db_path).with_name(Path(db_path).name + ".major-case.db")
        )
        attachment_root = (
            Path(major_attachment_root)
            if major_attachment_root is not None
            else major_db.with_name(major_db.stem + "_attachments")
        )
        major_runtime_db = major_db.with_name(major_db.name + ".runtime.db")
        testability_mutable_paths.extend([major_db, major_runtime_db, attachment_root])
        artifact_root = Path(major_artifact_root) if major_artifact_root is not None else root
        artifacts = JsonArtifactRepository(artifact_root)
        major_repository = MajorKnowledgeRepository(major_db, attachment_root)
        major_case_service = MajorCaseProductionService(
            major_repository,
            artifacts,
            major_runtime_db,
            provider=major_provider,
        )
        major_restore_service = MajorCaseRestoreService(major_repository, root)
        ensure_major_runtime_schema = getattr(
            major_case_service.store, "_init_schema", None
        )
        if callable(ensure_major_runtime_schema):
            testability_restore_hooks.append(ensure_major_runtime_schema)
        search = MajorPublishedCaseSearchAdapter(artifacts)
        historical_case_service = HistoricalCaseConsumerService(
            artifacts,
            repeat_search=search.search,
        )
        app.state.major_case_repository = major_repository
        app.state.major_case_production_service = major_case_service
        app.state.major_case_restore_service = major_restore_service
        app.state.historical_case_service = historical_case_service
        app.include_router(
            create_major_production_router(
                major_case_service,
                restore_service=major_restore_service,
            )
        )

    if "REPEAT_RISK" in domains:
        from quality_knowledge.web.repeat_risk_integration import RepeatWebFacade

        if repeat_web is None:
            repeat_db = primary_db.with_name(primary_db.name + ".repeat-risk.db")
            testability_mutable_paths.append(repeat_db)
            repeat_web = RepeatWebFacade.from_project(
                issue_repository=repository,
                repeat_db_path=repeat_db,
                project_root=root,
                case_service=historical_case_service,
            )
        app.state.repeat_risk_service = repeat_web
    else:
        repeat_web = None
        app.state.repeat_risk_service = None

    if "HARDWARE_CASE" in domains:
        hardware_db = (
            Path(hardware_case_db_path)
            if hardware_case_db_path is not None
            else Path(db_path).with_name("hardware_case_mvp.db")
        )
        testability_mutable_paths.append(hardware_db)
        hardware_case_repository = HardwareCaseRepository(hardware_db)
        hardware_case_service = HardwareCaseBackendService(hardware_case_repository)
        app.state.hardware_case_repository = hardware_case_repository
        app.state.hardware_case_service = hardware_case_service

        hardware_source_root = (
            Path(hardware_case_source_root)
            if hardware_case_source_root is not None
            else hardware_db.with_name(hardware_db.stem + "_sources")
        )
        testability_mutable_paths.append(hardware_source_root)
        hardware_case_source_store = HardwareCaseSourceStore(
            hardware_db,
            hardware_source_root,
        )
        app.state.hardware_case_source_store = hardware_case_source_store

        def intake_structurer() -> Any:
            if hardware_case_structurer is not None:
                return hardware_case_structurer
            from services.hardware_case_runtime_adapter import build_hardware_case_structurer
            return build_hardware_case_structurer()

        hardware_case_intake_service = HardwareCaseIntakeService(
            hardware_db,
            hardware_case_source_store,
            hardware_case_service,
            intake_structurer,
        )
        app.state.hardware_case_intake_service = hardware_case_intake_service

        hardware_tree_import_repository = HardwareTreeImportRepository(hardware_db)
        hardware_tree_root = (
            Path(hardware_tree_upload_dir)
            if hardware_tree_upload_dir is not None
            else hardware_db.with_name(hardware_db.stem + "_tree_uploads")
        )
        testability_mutable_paths.append(hardware_tree_root)
        hardware_tree_file_store = HardwareTreeImportFileStore(hardware_tree_root)
        app.state.hardware_tree_import_repository = hardware_tree_import_repository
        app.state.hardware_tree_file_store = hardware_tree_file_store

        app.include_router(
            create_hardware_tree_import_router(
                hardware_tree_import_repository,
                hardware_tree_file_store,
                host_role=app.state.hardware_case_host_role,
            )
        )
        app.include_router(
            create_hardware_case_router(
                hardware_case_service,
                source_store=hardware_case_source_store,
                intake_service=hardware_case_intake_service,
                host_role=app.state.hardware_case_host_role,
            )
        )

    if "QUALITY_ISSUE" in domains:
        from quality_knowledge.web.api_v2 import create_v2_router
        from quality_knowledge.web.p1_pages import create_p1_router

        if app.state.overall_shell_enabled:
            from quality_knowledge.web.overall_shell import create_overall_shell_router

            app.include_router(
                create_overall_shell_router(
                    task_provider=overall_task_provider,
                    hardware_case_host_role=app.state.hardware_case_host_role,
                )
            )

        app.include_router(
            create_v2_router(
                repository,
                stage_runner=stage_runner,
                initialization_status=status,
                analysis_runtime_status=analysis_runtime_status,
                repeat_web=repeat_web,
            )
        )
        app.include_router(create_p04_router(app.state.p04_service))
        app.include_router(create_public_scenario_router(app.state.p04_service))
        app.include_router(create_portrait_router(app.state.portrait_service))
        app.include_router(
            create_p0_insights_router(
                scenario_detail_service=app.state.p04_service,
                hardware_case_host_role=app.state.hardware_case_host_role,
            )
        )
        app.include_router(create_p1_router())

        from quality_knowledge.web.legacy_database_binding import validate_legacy_database

        configured_legacy_db = legacy_quality_issue_db_path or os.getenv(
            "LEGACY_QUALITY_ISSUE_DB_PATH"
        )
        # A normalized path collision is a composition error; the validator
        # raises LEGACY_P0_DATABASE_PATH_COLLISION before the host can serve.
        legacy_db, legacy_error = validate_legacy_database(db_path, configured_legacy_db)
        if legacy_error is None and legacy_db is not None:
            from quality_knowledge.web.app import create_legacy_quality_issue_router

            legacy_router, legacy_services = create_legacy_quality_issue_router(
                legacy_db,
                initialize_schema=False,
            )
            app.include_router(legacy_router)
            app.state.legacy_quality_issue_services = legacy_services
            app.state.legacy_quality_issue_status = {
                "ready": True,
                "code": "READY",
                "database_path": str(legacy_db),
            }

            from quality_knowledge.web.legacy_scenario_compat import (
                create_legacy_scenario_read_router,
            )

            legacy_scenario_router, legacy_scenario_repository = (
                create_legacy_scenario_read_router(legacy_db)
            )
            app.include_router(legacy_scenario_router)
            app.state.legacy_scenario_repository = legacy_scenario_repository
            app.state.legacy_scenario_status = legacy_scenario_repository.status()
            testability_mutable_paths.append(legacy_db)
        else:
            app.state.legacy_quality_issue_status = {
                "ready": False,
                "code": legacy_error or "LEGACY_DB_UNAVAILABLE",
                "database_path": str(legacy_db) if legacy_db is not None else None,
            }
            app.state.legacy_scenario_status = {
                "ready": False,
                "code": legacy_error or "LEGACY_DB_UNAVAILABLE",
                "database_path": str(legacy_db) if legacy_db is not None else None,
                "mode": "READ_ONLY",
            }

            @app.middleware("http")
            async def legacy_capability_unavailable(request: Request, call_next):
                status = app.state.legacy_quality_issue_status
                path = request.url.path
                legacy_prefixes = (
                    "/analysis", "/analysis-batch", "/api/analysis", "/api/analysis-agents",
                    "/import", "/imports", "/api/import", "/statistics", "/api/statistics",
                    "/api/common-capability-gaps", "/api/capability-gaps", "/api/issues",
                    "/issues", "/settings", "/product-reports", "/export",
                    "/insights/capability-gaps", "/static",
                    "/quality-scenarios", "/quality-scenario-assets",
                    "/api/legacy-scenarios",
                )
                if any(path == prefix or path.startswith(prefix + "/") for prefix in legacy_prefixes):
                    code = str(status.get("code") or "LEGACY_DB_UNAVAILABLE")
                    if "text/html" in request.headers.get("accept", ""):
                        return HTMLResponse(
                            f"<main><h1>历史质量能力暂不可用</h1><p>{code}</p></main>",
                            status_code=503,
                        )
                    return JSONResponse({"detail": code}, status_code=503)
                return await call_next(request)

        root_target = "/p0/issues"
    else:
        app.include_router(
            create_hardware_case_pages_router(
                hardware_case_host_role=app.state.hardware_case_host_role,
            )
        )
        root_target = "/p0/hardware-cases"

    effective_testability = testability_enabled
    if effective_testability is None:
        effective_testability = os.getenv("OVERALL_TESTABILITY_ENABLED", "").strip() == "1"
    app.state.overall_testability_adapter = None
    if effective_testability:
        if not app.state.overall_shell_enabled:
            raise ValueError("OVERALL_TESTABILITY_REQUIRES_FULL_PLATFORM")
        state_root_value = testability_state_root or os.getenv(
            "OVERALL_TESTABILITY_STATE_ROOT"
        )
        token_value = testability_token or os.getenv("OVERALL_TESTABILITY_TOKEN")
        if not state_root_value:
            raise ValueError("OVERALL_TESTABILITY_STATE_ROOT_REQUIRED")
        if not token_value:
            raise ValueError("OVERALL_TESTABILITY_TOKEN_REQUIRED")
        from quality_knowledge.web.overall_testability import (
            OverallTestabilityAdapter,
            create_overall_testability_router,
        )

        adapter = OverallTestabilityAdapter(
            state_root=state_root_value,
            token=token_value,
            mutable_paths=testability_mutable_paths,
            p0_repository=repository,
            hardware_case_service=getattr(app.state, "hardware_case_service", None),
            restore_hooks=testability_restore_hooks,
        )
        app.state.overall_testability_adapter = adapter
        app.include_router(create_overall_testability_router(adapter))

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(root_target)

    return app
