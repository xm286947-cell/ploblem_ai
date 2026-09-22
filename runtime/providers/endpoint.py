from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


class ProviderEndpointError(ValueError):
    """Invalid provider endpoint configuration before any HTTP request."""

    def __init__(self, reason: str, *, details: dict | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = {"reason": reason, **(details or {})}


class ProviderEndpointResolver:
    """Runtime-owned endpoint validation and construction."""

    SUPPORTED_SCHEMES = {"http", "https"}
    CHAT_COMPLETIONS_SUFFIX = "/chat/completions"

    @classmethod
    def validate_base_url(cls, base_url: str | None) -> str:
        raw = str(base_url or "").strip()
        if not raw:
            raise ProviderEndpointError("empty_base_url")
        if any(char.isspace() for char in raw):
            raise ProviderEndpointError("whitespace_in_base_url")

        try:
            parsed = urlsplit(raw)
        except ValueError as exc:
            raise ProviderEndpointError("malformed_url") from exc

        if not parsed.scheme:
            raise ProviderEndpointError("missing_scheme")
        if parsed.scheme.lower() not in cls.SUPPORTED_SCHEMES:
            raise ProviderEndpointError(
                "unsupported_scheme",
                details={"scheme": parsed.scheme.lower()},
            )
        if not parsed.netloc or not parsed.hostname:
            raise ProviderEndpointError("missing_host")
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ProviderEndpointError("malformed_port") from exc
        if parsed.query:
            raise ProviderEndpointError("query_not_allowed")
        if parsed.fragment:
            raise ProviderEndpointError("fragment_not_allowed")

        path = parsed.path.rstrip("/")
        return urlunsplit(
            (parsed.scheme.lower(), parsed.netloc, path, "", "")
        )

    @classmethod
    def chat_completions_url(cls, base_url: str | None) -> str:
        normalized = cls.validate_base_url(base_url)
        if normalized.endswith(cls.CHAT_COMPLETIONS_SUFFIX):
            return normalized
        return normalized + cls.CHAT_COMPLETIONS_SUFFIX


__all__ = ["ProviderEndpointError", "ProviderEndpointResolver"]
