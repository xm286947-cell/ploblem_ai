"""Hardware production IAM boundary.

Authentication/authorization is Hardware-owned policy at the HTTP boundary.
The implementation intentionally accepts only server-side configured tokens;
caller-provided role headers are never an authentication mechanism.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Mapping


class HardwareIAMError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class HardwarePrincipal:
    subject: str
    role: str
    authn_method: str = "BEARER_TOKEN"


class HardwareIAM:
    def __init__(self, tokens: Mapping[str, tuple[str, str]]):
        self._tokens = {
            self._digest(token): HardwarePrincipal(subject, role)
            for token, (subject, role) in tokens.items()
            if token and role in {"CONSUMER", "MAINTAINER"}
        }

    @classmethod
    def from_environment(cls) -> "HardwareIAM":
        tokens: dict[str, tuple[str, str]] = {}
        consumer = os.environ.get("HARDWARE_CONSUMER_TOKEN", "").strip()
        maintainer = os.environ.get("HARDWARE_MAINTAINER_TOKEN", "").strip()
        if consumer:
            tokens[consumer] = ("hardware-consumer", "CONSUMER")
        if maintainer:
            tokens[maintainer] = ("hardware-maintainer", "MAINTAINER")
        return cls(tokens)

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @property
    def configured(self) -> bool:
        return bool(self._tokens)

    def authenticate(self, authorization: str | None) -> HardwarePrincipal:
        value = str(authorization or "").strip()
        if not value.lower().startswith("bearer "):
            raise HardwareIAMError("AUTHENTICATION_REQUIRED")
        token = value[7:].strip()
        if not token:
            raise HardwareIAMError("AUTHENTICATION_REQUIRED")
        digest = self._digest(token)
        for expected, principal in self._tokens.items():
            if hmac.compare_digest(digest, expected):
                return principal
        raise HardwareIAMError("AUTHENTICATION_INVALID")

    def authorize(
        self,
        authorization: str | None,
        *,
        required_role: str,
    ) -> HardwarePrincipal:
        principal = self.authenticate(authorization)
        if required_role == "MAINTAINER" and principal.role != "MAINTAINER":
            raise HardwareIAMError("AUTHORIZATION_DENIED")
        return principal


__all__ = ["HardwareIAM", "HardwareIAMError", "HardwarePrincipal"]
