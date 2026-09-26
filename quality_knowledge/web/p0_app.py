"""FastAPI composition root for platform and product-scoped profiles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from quality_knowledge.web.hardware_case_api import create_hardware_case_router
from quality_knowledge.web.hardware_tree_import_api import create_hardware_tree_import_router
from quality_knowledge.web.p0_pages import (
    create_hardware_case_pages_router,
    create_p0_insights_router,
)
from quality_knowledge.p04.adapter import P04Provider, UnavailableP04Provider
from quality_knowledge.p04.api import create_p04_router
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
    hardware_case_structurer: Any | None = None,
    repeat_web: Any | None = None,
    p04_provider: P04Provider | None = None,
    portrait_provider: PortraitProvider | None = None,
    portrait_db_path: str | Path | None = None,
    major_context_provider: Any | None = None,
    enabled_domains: set[str] | frozenset[str] | None = None,
) -> FastAPI:
    """Build the shared Web host with explicit domain composition.

    The default remains the historical full platform.  Product test packages
    may request only HARDWARE_CASE; that profile reuses the same FastAPI host,
    /api/v2 surface, templates and port without initializing unrelated domains.
    """
    domains = _normalize_domains(enabled_domains)
    root = Path(project_root)
    app = FastAPI(title="Quality Capability P1", version="2.1.0")
    app.state.enabled_domains = tuple(sorted(domains))

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
    # P04 is intentionally provider-injected.  The default is explicit
    # DATA_UNAVAILABLE until the approved public JSON providers are wired.
    app.state.p04_provider = p04_provider or UnavailableP04Provider()
    app.state.p04_service = P04InsightService(app.state.p04_provider)
    app.state.portrait_provider = portrait_provider or UnavailablePortraitProvider()
    app.state.portrait_repository = PortraitArchiveRepository(
        portrait_db_path
        or Path(db_path).with_name(Path(db_path).stem + ".p04-portrait.db")
    )
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

    if "REPEAT_RISK" in domains:
        from quality_knowledge.web.repeat_risk_integration import RepeatWebFacade

        if repeat_web is None:
            repeat_db = Path(db_path).with_name(Path(db_path).name + ".repeat-risk.db")
            repeat_web = RepeatWebFacade.from_project(
                issue_repository=repository,
                repeat_db_path=repeat_db,
                project_root=root,
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
        hardware_case_repository = HardwareCaseRepository(hardware_db)
        hardware_case_service = HardwareCaseBackendService(hardware_case_repository)
        app.state.hardware_case_repository = hardware_case_repository
        app.state.hardware_case_service = hardware_case_service

        hardware_case_source_store = HardwareCaseSourceStore(
            hardware_db,
            Path(hardware_case_source_root)
            if hardware_case_source_root is not None
            else hardware_db.with_name(hardware_db.stem + "_sources"),
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
        hardware_tree_file_store = HardwareTreeImportFileStore(
            Path(hardware_tree_upload_dir)
            if hardware_tree_upload_dir is not None
            else hardware_db.with_name(hardware_db.stem + "_tree_uploads")
        )
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
            )
        )

    if "QUALITY_ISSUE" in domains:
        from quality_knowledge.web.api_v2 import create_v2_router
        from quality_knowledge.web.p1_pages import create_p1_router

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
        app.include_router(create_portrait_router(app.state.portrait_service))
        app.include_router(create_p0_insights_router())
        app.include_router(create_p1_router())
        root_target = "/p0/insights"
    else:
        app.include_router(create_hardware_case_pages_router())
        root_target = "/p0/hardware-cases"

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(root_target)

    return app
