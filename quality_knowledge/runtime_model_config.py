from __future__ import annotations

import os
from pathlib import Path


def resolve_major_runtime_model_config(
    root: str | Path,
    explicit: str | Path | None = None,
) -> Path:
    """Resolve the Major product Runtime model config.

    Precedence follows the Storage-proven Runtime usage rule:
    1. explicit caller path;
    2. MAJOR_MODEL_CONFIG;
    3. non-committed config/model.local.yaml;
    4. shared config/runtime/model.yaml.

    This helper only selects the model-config file. Provider credentials and
    secret injection remain owned by Unified Runtime.
    """

    project_root = Path(root).resolve()
    selected: str | Path | None = explicit
    if selected is None:
        configured = os.environ.get("MAJOR_MODEL_CONFIG", "").strip()
        if configured:
            selected = configured

    if selected is not None:
        path = Path(selected).expanduser()
        if not path.is_absolute():
            path = project_root / path
        path = path.resolve()
        if not path.is_file():
            raise ValueError(f"MAJOR_MODEL_CONFIG_NOT_FOUND:{path}")
        return path

    local_path = project_root / "config/model.local.yaml"
    if local_path.is_file():
        return local_path.resolve()

    return (project_root / "config/runtime/model.yaml").resolve()


__all__ = ["resolve_major_runtime_model_config"]
