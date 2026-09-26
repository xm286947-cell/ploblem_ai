"""Bind the existing Storage product host into the Overall Shell.

The binding is intentionally an ASGI mount, not a second Storage application.
The Storage product keeps ownership of its routes, database, Evidence and
KnowledgeRelease consumers; the Overall Shell only owns the workspace entry.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI


DEFAULT_STORAGE_WORKSPACE_PREFIX = "/storage-workspace"


def bind_storage_workspace(
    overall_app: FastAPI,
    *,
    storage_app: Any | None = None,
    prefix: str = DEFAULT_STORAGE_WORKSPACE_PREFIX,
) -> dict[str, Any]:
    """Mount the existing Storage Host once and return binding metadata.

    ``storage_app`` is injectable for tests and controlled composition. When
    omitted, the product's existing module-level FastAPI app is reused. This
    function never constructs another FastAPI app and never creates a second
    Storage database.
    """

    normalized_prefix = "/" + prefix.strip("/")
    if normalized_prefix == "/":
        raise ValueError("STORAGE_WORKSPACE_PREFIX_MUST_NOT_BE_ROOT")

    if storage_app is None:
        from products.storage_rc1.storage_life.app import app as storage_app

    existing = next(
        (
            route
            for route in overall_app.routes
            if getattr(route, "path", None) == normalized_prefix
        ),
        None,
    )
    if existing is not None:
        mounted_app = getattr(existing, "app", None)
        if mounted_app is storage_app:
            return {
                "prefix": normalized_prefix,
                "storage_app": storage_app,
                "storage_db": _storage_db_path(storage_app),
            }
        raise ValueError("STORAGE_WORKSPACE_PREFIX_ALREADY_BOUND")

    overall_app.mount(normalized_prefix, storage_app, name="storage-workspace")
    return {
        "prefix": normalized_prefix,
        "storage_app": storage_app,
        "storage_db": _storage_db_path(storage_app),
    }


def _storage_db_path(storage_app: Any) -> str | None:
    """Expose the existing product DB path for diagnostics without owning it."""

    try:
        from products.storage_rc1.storage_life import core

        return str(core.DB)
    except (ImportError, AttributeError):
        return None
