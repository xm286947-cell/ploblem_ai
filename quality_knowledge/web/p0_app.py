"""FastAPI composition root for platform and product-scoped profiles."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from quality_knowledge.web.hardware_case_api import create_hardware_case_router
from quality_knowledge.web.hardware_public_api import create_hardware_public_router
from quality_knowledge.web.hardware_knowledge_consumption_api import (
    create_hardware_knowledge_consumption_router,
)
from quality_knowledge.web.hardware_r1_e2e_api import create_hardware_r1_e2e_router
from quality_knowledge.web.hardware_operability_api import create_hardware_operability_router
from quality_knowledge.web.hardware_r1_workbench_api import create_hardware_r1_workbench_router
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
from services.hardware_asset_repository import CandidateAssetRepository
from services.hardware_asset_operation_journal import HardwareAssetOperationJournal
from services.hardware_case_backend import HardwareCaseBackendService
from services.hardware_case_intake import HardwareCaseIntakeService
from services.hardware_case_knowledge_adapter import (
    HardwareCaseKnowledgeAdapter,
    KnowledgeHttpTransport,
)
from services.hardware_case_source_store import HardwareCaseSourceStore
from services.hardware_knowledge_consumption import (
    PROJECTION_FILENAME,
    HardwareKnowledgeConsumptionProjectionStore,
    HardwareKnowledgeConsumptionService,
)
from services.hardware_case_r1_preview_store import HardwareR1PreviewStore
from services.hardware_case_r1_workbench import (
    HardwareR1WorkbenchService,
    HardwareR1WorkbenchStore,
)
from services.hardware_case_r1_runtime import invalidate_hardware_r1_stage_cache
from services.hardware_r1_golden_knowledge_bridge import HardwareR1GoldenKnowledgeBridge
from services.hardware_r1_knowledge_promotion import (
    HardwareR1KnowledgePromotionService,
    HardwareR1KnowledgePromotionStore,
    HardwareR1PromotionError,
)
from services.hardware_r1_e2e_nonprod_knowledge import (
    HardwareR1ManagedNonProdError,
    create_managed_nonprod_environment,
)
from services.hardware_data_reliability import (
    HardwareDataReliabilityError,
    HardwareDataReliabilityManager,
)
from services.hardware_data_root import HardwareDataRootResolver
from services.hardware_durable_mutation_gate import (
    HardwareApplicationLock,
    HardwareDurableMutationError,
    HardwareDurableMutationGate,
)
from services.hardware_tree_import_files import HardwareTreeImportFileStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORAGE_WORKSPACE_PREFIX = "/storage-workspace"
FULL_DOMAINS = frozenset({"QUALITY_ISSUE", "REPEAT_RISK", "HARDWARE_CASE"})
KNOWN_DOMAINS = FULL_DOMAINS


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
    hardware_r1_workbench_db_path: str | Path | None = None,
    hardware_r1_preview_db_path: str | Path | None = None,
    hardware_startup_status: dict[str, Any] | None = None,
    hardware_case_structurer: Any | None = None,
    hardware_case_r1_structurer: Any | None = None,
    hardware_knowledge_adapter: Any | None = None,
    hardware_knowledge_base_url: str | None = None,
    hardware_knowledge_release_version: str | None = None,
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
    configured_hardware_db = (
        Path(hardware_case_db_path)
        if hardware_case_db_path is not None
        else primary_db.with_name("hardware_case_mvp.db")
    )
    hardware_data_root = (
        configured_hardware_db.parent.parent
        if configured_hardware_db.parent.name == "db"
        else configured_hardware_db.parent
    ).resolve(strict=False)
    hardware_mutation_gate = HardwareDurableMutationGate(hardware_data_root)

    @asynccontextmanager
    async def hardware_lifespan(application: FastAPI):
        lease: HardwareApplicationLock | None = None
        if "HARDWARE_CASE" in domains and (
            hardware_startup_status is None or hardware_startup_status.get("ready")
        ):
            lease = HardwareApplicationLock(hardware_data_root)
            lease.acquire()
            application.state.hardware_application_lock = lease
        try:
            yield
        finally:
            if lease is not None:
                lease.release()

    testability_mutable_paths: list[Path] = [primary_db]
    testability_restore_hooks: list[Any] = []
    app = FastAPI(title="Quality Capability P1", version="2.1.0", lifespan=hardware_lifespan)
    app.state.hardware_durable_mutation_gate = hardware_mutation_gate

    @app.middleware("http")
    async def hardware_durable_mutation_middleware(request: Request, call_next: Any):
        hardware_path = request.url.path.startswith(
            (
                "/api/v2/hardware-cases",
                "/api/public/hardware/v1",
                "/p0/hardware-cases",
            )
        )
        if (
            "HARDWARE_CASE" in domains
            and hardware_path
            and request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
        ):
            try:
                with hardware_mutation_gate.mutation():
                    return await call_next(request)
            except HardwareDurableMutationError as error:
                return JSONResponse(
                    status_code=error.http_status,
                    content={"detail": error.code},
                )
        return await call_next(request)
    app.state.enabled_domains = tuple(sorted(domains))
    app.state.overall_shell_enabled = domains == FULL_DOMAINS
    app.state.storage_workspace_binding = None
    app.state.hardware_startup_status = hardware_startup_status
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
        app.state.historical_case_service = historical_case_service
        app.include_router(create_major_production_router(major_case_service))

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

    # Operability /ready is mounted before the Hardware Knowledge binding is
    # fully composed.  Keep a shared live status mapping so the route reports
    # the actual in-process LOCAL_NON_PROD state instead of requiring external
    # Knowledge URLs that are intentionally absent in the E2E profile.
    hardware_operability_knowledge_status: dict[str, Any] = {}

    if "HARDWARE_CASE" in domains and hardware_startup_status is not None and not hardware_startup_status.get("ready"):
        hardware_db = (
            Path(hardware_case_db_path)
            if hardware_case_db_path is not None
            else Path(db_path).with_name("hardware_case_mvp.db")
        )
        app.include_router(
            create_hardware_operability_router(
                project_root=root,
                hardware_db_path=hardware_db,
                startup_status=hardware_startup_status,
                knowledge_status=hardware_operability_knowledge_status,
            )
        )
        app.state.hardware_data_reliability = None
        app.state.hardware_data_status = dict(hardware_startup_status)
        app.state.hardware_case_repository = None
        app.state.hardware_case_service = None
        app.state.hardware_case_source_store = None
        app.state.hardware_candidate_asset_repository = None
        app.state.hardware_asset_operation_journal = None
        app.state.hardware_case_intake_service = None
        app.state.hardware_r1_preview_store = None
        app.state.hardware_r1_workbench_store = None
        app.state.hardware_r1_workbench_service = None
        app.state.hardware_r1_promotion_store = None
        app.state.hardware_r1_promotion_service = None
        app.state.hardware_r1_promotion_status = {
            "ready": False,
            "code": str(hardware_startup_status.get("error_code") or "HARDWARE_STARTUP_NOT_READY"),
            "auto_publish": False,
        }
        app.state.hardware_knowledge_consumption_service = None
        app.state.hardware_tree_import_repository = None
        app.state.hardware_tree_file_store = None

    if "HARDWARE_CASE" in domains and (hardware_startup_status is None or hardware_startup_status.get("ready")):
        hardware_db = (
            Path(hardware_case_db_path)
            if hardware_case_db_path is not None
            else Path(db_path).with_name("hardware_case_mvp.db")
        )
        testability_mutable_paths.append(hardware_db)
        hardware_data = HardwareDataReliabilityManager(hardware_db)
        app.state.hardware_data_reliability = hardware_data

        try:
            hardware_data_status = hardware_data.ensure_ready()
            hardware_data_ready = True
        except HardwareDataReliabilityError as error:
            hardware_data_status = {
                **hardware_data.inspect_status(),
                "startup_error": error.code,
                "backup_id": error.backup_id,
            }
            hardware_data_ready = False
        app.state.hardware_data_status = hardware_data_status

        # Health remains available even when Hardware data is blocked. Readiness
        # reports the schema/recovery failure and Hardware business routes are
        # not mounted until the DB reaches a verified READY state.
        app.include_router(
            create_hardware_operability_router(
                project_root=root,
                hardware_db_path=hardware_db,
                startup_status=hardware_startup_status,
                knowledge_status=hardware_operability_knowledge_status,
            )
        )

        if hardware_data_ready:
            consumption_projection = HardwareKnowledgeConsumptionProjectionStore(
                hardware_data_root / "rebuildable" / PROJECTION_FILENAME
            )
            hardware_knowledge_consumption_service = (
                HardwareKnowledgeConsumptionService(consumption_projection)
            )
            app.state.hardware_knowledge_consumption_service = (
                hardware_knowledge_consumption_service
            )

            hardware_case_repository = HardwareCaseRepository(
                hardware_db,
                initialize_schema=False,
            )
            hardware_case_service = HardwareCaseBackendService(hardware_case_repository)
            app.state.hardware_case_repository = hardware_case_repository
            app.state.hardware_case_service = hardware_case_service

            hardware_source_root = (
                Path(hardware_case_source_root)
                if hardware_case_source_root is not None
                else hardware_db.with_name(hardware_db.stem + "_sources")
            )
            testability_mutable_paths.append(hardware_source_root)
            hardware_asset_db = hardware_db.with_name("hardware_asset.db")
            hardware_candidate_asset_repository = CandidateAssetRepository(hardware_asset_db)
            hardware_operation_journal = HardwareAssetOperationJournal(hardware_asset_db)
            hardware_case_source_store = HardwareCaseSourceStore(
                hardware_db,
                hardware_source_root,
                initialize_schema=False,
                operation_journal=hardware_operation_journal,
                candidate_repository=hardware_candidate_asset_repository,
            )
            app.state.hardware_candidate_asset_repository = hardware_candidate_asset_repository
            app.state.hardware_asset_operation_journal = hardware_operation_journal
            app.state.hardware_case_source_store = hardware_case_source_store

            def intake_structurer() -> Any:
                if hardware_case_structurer is not None:
                    return hardware_case_structurer
                from services.hardware_case_runtime_adapter import build_hardware_case_structurer
                return build_hardware_case_structurer()

            def r1_structurer() -> Any:
                if hardware_case_r1_structurer is not None:
                    return hardware_case_r1_structurer
                from services.hardware_case_r1_runtime import build_hardware_case_r1_structurer
                return build_hardware_case_r1_structurer()

            hardware_case_intake_service = HardwareCaseIntakeService(
                hardware_db,
                hardware_case_source_store,
                hardware_case_service,
                intake_structurer,
                initialize_schema=False,
            )
            app.state.hardware_case_intake_service = hardware_case_intake_service

            hardware_r1_preview_db = (
                Path(hardware_r1_preview_db_path)
                if hardware_r1_preview_db_path is not None
                else hardware_db.with_name(hardware_db.stem + "_r1_preview.db")
            )
            hardware_r1_preview_store = HardwareR1PreviewStore(hardware_r1_preview_db)
            app.state.hardware_r1_preview_store = hardware_r1_preview_store
            testability_mutable_paths.append(hardware_r1_preview_db)

            hardware_r1_workbench_db = (
                Path(hardware_r1_workbench_db_path)
                if hardware_r1_workbench_db_path is not None
                else hardware_db.with_name(hardware_db.stem + "_r1_workbench.db")
            )
            hardware_r1_workbench_store = HardwareR1WorkbenchStore(
                hardware_r1_workbench_db
            )
            hardware_r1_workbench_service = HardwareR1WorkbenchService(
                hardware_r1_workbench_store,
                source_store=hardware_case_source_store,
                structurer_factory=r1_structurer,
                preview_store=hardware_r1_preview_store,
                candidate_repository=hardware_candidate_asset_repository,
            )
            app.state.hardware_r1_workbench_store = hardware_r1_workbench_store
            app.state.hardware_r1_workbench_service = hardware_r1_workbench_service
            testability_mutable_paths.append(hardware_r1_workbench_db)

            effective_knowledge_adapter = hardware_knowledge_adapter
            knowledge_base_url = str(
                hardware_knowledge_base_url
                or os.getenv("HARDWARE_KNOWLEDGE_BASE_URL")
                or ""
            ).strip()
            knowledge_release_version = str(
                hardware_knowledge_release_version
                or os.getenv("HARDWARE_KNOWLEDGE_RELEASE_VERSION")
                or ""
            ).strip()
            e2e_profile = os.getenv("HARDWARE_R1_E2E_PROFILE") == "1"
            e2e_knowledge_environment = os.getenv(
                "HARDWARE_R1_E2E_KNOWLEDGE_ENV", ""
            ).strip().upper()
            e2e_knowledge_mode = os.getenv(
                "HARDWARE_R1_E2E_KNOWLEDGE_MODE", "EXTERNAL"
            ).strip().upper()
            managed_nonprod_release_controller = None
            managed_nonprod_status: dict[str, Any] | None = None
            hardware_r1_promotion_service = None

            if (
                effective_knowledge_adapter is None
                and e2e_profile
                and e2e_knowledge_environment == "NON_PROD"
                and e2e_knowledge_mode == "LOCAL_NON_PROD"
                and knowledge_release_version
            ):
                try:
                    (
                        effective_knowledge_adapter,
                        managed_nonprod_release_controller,
                        managed_nonprod_status,
                    ) = create_managed_nonprod_environment(
                        hardware_data_root / "nonprod_unified_knowledge",
                        release_prefix=knowledge_release_version,
                    )
                except HardwareR1ManagedNonProdError as error:
                    effective_knowledge_adapter = None
                    managed_nonprod_release_controller = None
                    managed_nonprod_status = {
                        "mode": "LOCAL_NON_PROD",
                        "managed_release": True,
                        "ready": False,
                        "code": error.code,
                    }
            elif effective_knowledge_adapter is None and knowledge_base_url and knowledge_release_version:
                effective_knowledge_adapter = HardwareCaseKnowledgeAdapter(
                    KnowledgeHttpTransport(knowledge_base_url),
                    knowledge_release_version=knowledge_release_version,
                )

            local_nonprod_ready = managed_nonprod_release_controller is not None
            external_nonprod_ready = bool(
                e2e_knowledge_environment == "NON_PROD"
                and knowledge_base_url
                and knowledge_release_version
            )
            if e2e_profile:
                if local_nonprod_ready:
                    hardware_r1_knowledge_environment_status = {
                        "ready": True,
                        "environment": "NON_PROD",
                        **(managed_nonprod_status or {}),
                    }
                elif external_nonprod_ready:
                    hardware_r1_knowledge_environment_status = {
                        "ready": True,
                        "environment": "NON_PROD",
                        "mode": "EXTERNAL",
                        "managed_release": False,
                        "release_version": knowledge_release_version,
                    }
                else:
                    hardware_r1_knowledge_environment_status = {
                        "ready": False,
                        "environment": (
                            e2e_knowledge_environment or "UNCONFIGURED"
                        ),
                        "mode": e2e_knowledge_mode or "UNCONFIGURED",
                        "managed_release": e2e_knowledge_mode == "LOCAL_NON_PROD",
                        "release_version": None,
                        "code": (
                            (managed_nonprod_status or {}).get("code")
                            or "BLOCKED_BY_ENVIRONMENT"
                        ),
                    }
            else:
                hardware_r1_knowledge_environment_status = {
                    "ready": effective_knowledge_adapter is not None,
                    "environment": "STANDARD",
                    "mode": "EXTERNAL_OR_INJECTED",
                    "managed_release": False,
                    "release_version": knowledge_release_version or None,
                    "code": (
                        None
                        if effective_knowledge_adapter is not None
                        else "KNOWLEDGE_CONFIG_REQUIRED"
                    ),
                }
            app.state.hardware_r1_knowledge_environment_status = (
                hardware_r1_knowledge_environment_status
            )
            hardware_operability_knowledge_status.clear()
            hardware_operability_knowledge_status.update(
                hardware_r1_knowledge_environment_status
            )
            if effective_knowledge_adapter is not None:
                hardware_r1_promotion_store = HardwareR1KnowledgePromotionStore(
                    hardware_r1_workbench_db, read_only=True
                )
                hardware_r1_promotion_bridge = HardwareR1GoldenKnowledgeBridge(
                    effective_knowledge_adapter,
                    hardware_case_source_store,
                )
                hardware_r1_promotion_service = HardwareR1KnowledgePromotionService(
                    hardware_r1_promotion_store,
                    workbench_service=hardware_r1_workbench_service,
                    bridge=hardware_r1_promotion_bridge,
                    candidate_repository=hardware_candidate_asset_repository,
                )
                hardware_knowledge_consumption_service.assets = hardware_candidate_asset_repository
                hardware_knowledge_consumption_service.adapter = effective_knowledge_adapter
                app.state.hardware_r1_promotion_store = hardware_r1_promotion_store
                try:
                    migration_status = hardware_r1_promotion_service.migrate_legacy_records()
                except HardwareR1PromotionError as error:
                    hardware_r1_promotion_service = None
                    app.state.hardware_r1_promotion_service = None
                    app.state.hardware_r1_promotion_status = {
                        "ready": False,
                        "code": error.code,
                        "auto_publish": False,
                    }
                else:
                    try:
                        remote_recovery = hardware_r1_promotion_service.reconcile_startup(
                            max_remote_queries=2
                        )
                    except HardwareR1PromotionError as error:
                        remote_recovery = {
                            "pending_remote_reconciliation_count": 0,
                            "blocked_asset_count": 0,
                            "last_recovery_error": error.code,
                            "recovery_status": "DEGRADED",
                            "startup_queries_used": 0,
                        }
                    app.state.hardware_r1_promotion_service = hardware_r1_promotion_service
                    app.state.hardware_r1_promotion_status = {
                        "ready": True,
                        "code": (
                            "READY_DEGRADED"
                            if remote_recovery.get("pending_remote_reconciliation_count")
                            or remote_recovery.get("last_recovery_error")
                            else "READY"
                        ),
                        "legacy_migration": migration_status,
                        "remote_recovery": remote_recovery,
                        "auto_publish": False,
                    }
                    app.state.hardware_data_status.update(
                        {
                            "pending_remote_reconciliation_count": remote_recovery.get(
                                "pending_remote_reconciliation_count", 0
                            ),
                            "blocked_asset_count": remote_recovery.get(
                                "blocked_asset_count", 0
                            ),
                        }
                    )
                    if hardware_startup_status is not None:
                        local_recovery_pending = bool(
                            hardware_startup_status.get("pending_local_recovery_count")
                        )
                        remote_recovery_pending = bool(
                            remote_recovery.get("pending_remote_reconciliation_count")
                            or remote_recovery.get("last_recovery_error")
                        )
                        hardware_startup_status.update(
                            {
                                "pending_remote_reconciliation_count": remote_recovery.get(
                                    "pending_remote_reconciliation_count", 0
                                ),
                                "blocked_asset_count": remote_recovery.get(
                                    "blocked_asset_count", 0
                                ),
                                "recovery_class": (
                                    "CLASS_A"
                                    if local_recovery_pending
                                    else "CLASS_B"
                                    if remote_recovery_pending
                                    else None
                                ),
                                "recovery_status": (
                                    "DEGRADED"
                                    if remote_recovery_pending or local_recovery_pending
                                    else "COMPLETED"
                                ),
                                "degraded": remote_recovery_pending or local_recovery_pending,
                                "last_recovery_error": (
                                    remote_recovery.get("last_recovery_error")
                                    or (
                                        hardware_startup_status.get("last_recovery_error")
                                        if local_recovery_pending
                                        else None
                                    )
                                ),
                            }
                        )
            else:
                app.state.hardware_r1_promotion_store = None
                app.state.hardware_r1_promotion_service = None
                app.state.hardware_r1_promotion_status = {
                    "ready": False,
                    "code": (
                        "KNOWLEDGE_CONFIG_INCOMPLETE"
                        if knowledge_base_url or knowledge_release_version
                        else "KNOWLEDGE_PROMOTION_UNAVAILABLE"
                    ),
                    "auto_publish": False,
                }

            hardware_tree_import_repository = HardwareTreeImportRepository(
                hardware_db,
                initialize_schema=False,
            )
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
                )
            )
            app.include_router(
                create_hardware_case_router(
                    hardware_case_service,
                    source_store=hardware_case_source_store,
                    intake_service=hardware_case_intake_service,
                    r1_structurer_factory=r1_structurer,
                    r1_preview_store=hardware_r1_preview_store,
                    r1_stage_cache_invalidator=lambda source_id, project_root=root: (
                        invalidate_hardware_r1_stage_cache(
                            source_id,
                            root=project_root,
                        )
                    ),
                )
            )
            app.include_router(
                create_hardware_r1_workbench_router(
                    hardware_r1_workbench_service,
                    promotion_service=hardware_r1_promotion_service,
                    consumption_service=hardware_knowledge_consumption_service,
                    publish_allowed=(
                        not e2e_profile
                        or (
                            e2e_knowledge_environment == "NON_PROD"
                            and (
                                managed_nonprod_release_controller is not None
                                or (
                                    bool(knowledge_base_url)
                                    and bool(knowledge_release_version)
                                )
                            )
                        )
                    ),
                    release_controller=managed_nonprod_release_controller,
                )
            )
            app.include_router(create_hardware_public_router(hardware_case_service))
            app.include_router(
                create_hardware_knowledge_consumption_router(
                    hardware_knowledge_consumption_service,
                    source_store=hardware_case_source_store,
                    knowledge_adapter=effective_knowledge_adapter,
                )
            )
            if os.getenv("HARDWARE_R1_E2E_PROFILE") == "1":
                app.include_router(
                    create_hardware_r1_e2e_router(
                        app_root=root,
                        data_root=hardware_data_root,
                        normal_data_root=HardwareDataRootResolver(root).default_data_root,
                        promotion_status=app.state.hardware_r1_promotion_status,
                        knowledge_status=hardware_r1_knowledge_environment_status,
                    )
                )
            testability_restore_hooks.append(hardware_data.ensure_ready)
        else:
            app.state.hardware_case_repository = None
            app.state.hardware_case_service = None
            app.state.hardware_case_source_store = None
            app.state.hardware_case_intake_service = None
            app.state.hardware_r1_preview_store = None
            app.state.hardware_r1_workbench_store = None
            app.state.hardware_r1_workbench_service = None
            app.state.hardware_r1_promotion_store = None
            app.state.hardware_r1_promotion_service = None
            app.state.hardware_r1_promotion_status = {
                "ready": False,
                "code": "HARDWARE_DATA_NOT_READY",
                "auto_publish": False,
            }
            app.state.hardware_knowledge_consumption_service = None
            app.state.hardware_tree_import_repository = None
            app.state.hardware_tree_file_store = None

    if "QUALITY_ISSUE" in domains:
        from quality_knowledge.web.api_v2 import create_v2_router
        from quality_knowledge.web.p1_pages import create_p1_router

        if app.state.overall_shell_enabled:
            from quality_knowledge.web.overall_shell import create_overall_shell_router

            app.include_router(
                create_overall_shell_router(task_provider=overall_task_provider)
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
            create_p0_insights_router(scenario_detail_service=app.state.p04_service)
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
            testability_mutable_paths.append(legacy_db)
        else:
            app.state.legacy_quality_issue_status = {
                "ready": False,
                "code": legacy_error or "LEGACY_DB_UNAVAILABLE",
                "database_path": str(legacy_db) if legacy_db is not None else None,
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
        if hardware_startup_status is None or hardware_startup_status.get("ready"):
            app.include_router(create_hardware_case_pages_router())
            root_target = "/p0/hardware-cases"
        else:
            root_target = "/ready"

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
