"""Read-only readiness endpoint used by the single-package E2E landing page."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter


def create_hardware_r1_e2e_router(*, app_root: str | Path, data_root: str | Path, normal_data_root: str | Path, promotion_status: dict[str, Any]) -> APIRouter:
    router = APIRouter(prefix="/api/e2e/hardware-r1", tags=["hardware-r1-e2e-readiness"])
    root = Path(app_root).resolve()
    persistent = Path(data_root).resolve()
    normal = Path(normal_data_root).resolve()

    @router.get("/readiness")
    def readiness() -> dict[str, Any]:
        model_path = Path(os.getenv("HARDWARE_CASE_MODEL_CONFIG") or root / "config/runtime/model.local.yaml").expanduser()
        agent_path = Path(os.getenv("HARDWARE_CASE_R1_AGENT_CONFIG") or root / "config/runtime/agents/hardware_case.r1_extract.yaml").expanduser()
        model_ok = model_path.is_file()
        agent_ok = agent_path.is_file()
        nonprod = (
            os.getenv("HARDWARE_R1_E2E_KNOWLEDGE_ENV", "").strip().upper() == "NON_PROD"
            and bool(os.getenv("HARDWARE_KNOWLEDGE_BASE_URL", "").strip())
            and bool(os.getenv("HARDWARE_KNOWLEDGE_RELEASE_VERSION", "").strip())
        )
        return {
            "profile": "HARDWARE_R1_SINGLE_PACKAGE_E2E",
            "data_root_isolated": (
                persistent != root and root not in persistent.parents and persistent not in root.parents
                and persistent != normal and normal not in persistent.parents and persistent not in normal.parents
            ),
            "normal_data_root_untouched_by_profile": True,
            "agent_config": "READY" if agent_ok else "BLOCKED",
            "agent_config_code": None if agent_ok else "R1_AGENT_CONFIG_REQUIRED",
            "model_provider_config": "READY" if model_ok else "BLOCKED",
            "model_provider_config_code": None if model_ok else "MODEL_LOCAL_CONFIG_REQUIRED",
            "provider_secrets": "ENVIRONMENT_REFERENCES_ONLY",
            "provider_call_performed": False,
            "knowledge_environment": "NON_PROD" if nonprod else "BLOCKED_BY_ENVIRONMENT",
            "promotion_service": "READY" if promotion_status.get("ready") else "BLOCKED",
            "promotion_code": promotion_status.get("code"),
            "auto_publish": False,
        }

    return router
