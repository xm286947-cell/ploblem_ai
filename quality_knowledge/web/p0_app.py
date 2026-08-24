"""FastAPI factory for the clean P0 database."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from quality_knowledge.model_config import ModelConfigError, load_quality_issue_ai_config, validate_quality_issue_ai_config
from quality_knowledge.p0.initializer import P0InitializationError, P0Initializer
from quality_knowledge.p0.repository import P0Repository
from quality_knowledge.p0.stage_runner import ProductionV2StageRunner
from quality_knowledge.web.api_v2 import create_v2_router
from quality_knowledge.web.p0_pages import create_p0_insights_router
from quality_knowledge.web.p1_pages import create_p1_router


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def create_p0_app(
    db_path: str | Path,
    *,
    stage_runner: Any | None = None,
    project_root: str | Path = PROJECT_ROOT,
) -> FastAPI:
    root = Path(project_root)
    app = FastAPI(title="Quality Capability P1", version="2.1.0")
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
    analysis_runtime_status: dict[str, Any]
    if stage_runner is None:
        try:
            diagnostic = validate_quality_issue_ai_config(root, require_enabled=True)
            if diagnostic["ok"]:
                ai_config, _ = load_quality_issue_ai_config(root)
                stage_runner = ProductionV2StageRunner(repository, ai_config=ai_config)
            analysis_runtime_status = {
                **diagnostic,
                "ready": stage_runner is not None,
                "source": "MODEL_CONFIG",
            }
        except (ModelConfigError, ValueError) as error:
            stage_runner = None
            analysis_runtime_status = {
                "ready": False,
                "source": "MODEL_CONFIG",
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
    app.include_router(create_v2_router(
        repository,
        stage_runner=stage_runner,
        initialization_status=status,
        analysis_runtime_status=analysis_runtime_status,
    ))
    app.include_router(create_p0_insights_router())
    app.include_router(create_p1_router())

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/p0/insights")

    return app
