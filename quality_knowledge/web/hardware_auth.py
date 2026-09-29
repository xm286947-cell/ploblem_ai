"""Trusted server-side authorization context for Hardware Case.

Browser-controlled query parameters, Web Storage and headers are never authority.
The host role is injected by the server composition boundary; when it is absent,
Hardware fails closed to CONSUMER.
"""
from __future__ import annotations

from dataclasses import dataclass
import os

from fastapi import HTTPException


_ALLOWED_ROLES = {"CONSUMER", "MAINTAINER"}


def _normalize_role(value: str | None) -> str:
    role = str(value or "CONSUMER").strip().upper()
    if role not in _ALLOWED_ROLES:
        raise ValueError("HARDWARE_CASE_HOST_ROLE_INVALID")
    return role


@dataclass(frozen=True)
class HardwareTrustedAuthContext:
    role: str
    actor: str

    def resolve_read_role(self, client_claim: str | None = None) -> str:
        """Allow optional de-escalation but never client-side escalation."""
        if client_claim is None or not str(client_claim).strip():
            # Existing consumer APIs remain consumer-by-default even on a
            # maintainer host.  Maintainer read visibility requires an explicit
            # client claim that the trusted host is allowed to honor.
            return "CONSUMER"
        claim = str(client_claim).strip().upper()
        if claim not in _ALLOWED_ROLES:
            raise HTTPException(status_code=403, detail="HARDWARE_CASE_ROLE_INVALID")
        if claim == "MAINTAINER" and self.role != "MAINTAINER":
            raise HTTPException(
                status_code=403,
                detail="HARDWARE_CASE_MAINTAINER_REQUIRED",
            )
        return claim

    def require_maintainer(self, client_claim: str | None = None) -> str:
        claim = str(client_claim or "CONSUMER").strip().upper()
        if claim not in _ALLOWED_ROLES:
            raise HTTPException(status_code=403, detail="HARDWARE_CASE_ROLE_INVALID")
        if self.role != "MAINTAINER" or claim != "MAINTAINER":
            raise HTTPException(
                status_code=403,
                detail="HARDWARE_CASE_MAINTAINER_REQUIRED",
            )
        return "MAINTAINER"


def trusted_hardware_auth_context(
    host_role: str | None,
    *,
    host_actor: str | None = None,
) -> HardwareTrustedAuthContext:
    role = _normalize_role(host_role)
    raw_actor = (
        host_actor
        if host_actor is not None
        else os.getenv("HARDWARE_CASE_HOST_ACTOR")
    )
    actor = str(raw_actor or "").strip()
    if not actor:
        actor = (
            "server:hardware-maintainer"
            if role == "MAINTAINER"
            else "server:hardware-consumer"
        )
    return HardwareTrustedAuthContext(role=role, actor=actor)
