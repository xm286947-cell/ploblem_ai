"""Web package public factories with lazy imports.

A product-specific Web composition must not load every business Web module
merely by importing quality_knowledge.web.
"""

from __future__ import annotations

from typing import Any


__all__ = ["create_app", "create_p0_app"]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from .app import create_app
        globals()[name] = create_app
        return create_app
    if name == "create_p0_app":
        from .p0_app import create_p0_app
        globals()[name] = create_p0_app
        return create_p0_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
