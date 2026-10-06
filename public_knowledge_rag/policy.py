from __future__ import annotations

import ipaddress
import re
from urllib.parse import parse_qsl, urlparse

from fastapi import HTTPException


_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I),
    re.compile(r"\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*\S+", re.I),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.I),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
]
_PRIVATE_QUERY_PATTERNS = [
    # Block explicit private business identifiers, not ordinary public
    # engineering terminology.  Phrases such as "internal ECC" describe a
    # device capability and must remain searchable.
    re.compile(
        r"\b(?:customer|project|internal|confidential|restricted)"
        r"(?:[_\s-]*(?:id|name|code|ticket|case))?\s*[:=#]\s*\S+",
        re.I,
    ),
    re.compile(
        r"\b(?:customer|project|internal)\s+(?:id|name|code|ticket|case)\s+[A-Za-z0-9._-]{3,}\b",
        re.I,
    ),
    re.compile(
        r"(?:客户|项目|内部|机密|保密|工单|问题单|现场问题)"
        r"(?:名|名称|编号|ID|id|号)?\s*[:：=#]\s*\S+"
    ),
    re.compile(
        r"(?:客户|项目|工单|问题单)(?:名|名称|编号|ID|id|号)\s+[A-Za-z0-9._-]{3,}"
    ),
    re.compile(
        r"\b(?:S/?N|serial(?:\s*(?:number|no\.?))?)\s*[:#=]\s*[A-Z0-9-]{4,}\b",
        re.I,
    ),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
]


def contains_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_PATTERNS)


def require_public_source(classification: str, content: str, source_uri: str | None) -> None:
    if classification.strip().upper() != "PUBLIC":
        raise HTTPException(422, "Only sources explicitly classified PUBLIC may be imported.")
    if contains_secret(content) or contains_secret(source_uri or ""):
        raise HTTPException(422, "Source rejected by credential protection policy.")
    if source_uri:
        parsed = urlparse(source_uri)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise HTTPException(422, "Source URI must be an HTTP(S) public URL.")
        host = parsed.hostname.lower()
        if host in {"localhost", "127.0.0.1", "::1"} or host.endswith((".local", ".internal")):
            raise HTTPException(422, "Private or local source URLs are not accepted.")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise HTTPException(422, "Private or reserved source IP addresses are not accepted.")
        sensitive_query_keys = {"key", "token", "access_token", "auth", "password", "secret", "credential"}
        if any(key.lower() in sensitive_query_keys for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
            raise HTTPException(422, "Source URI contains credential-like query parameters.")


def require_public_query(query: str) -> None:
    if contains_secret(query):
        raise HTTPException(422, "Outbound query rejected by credential protection policy.")
    if any(pattern.search(query) for pattern in _PRIVATE_QUERY_PATTERNS):
        raise HTTPException(422, "Outbound query contains private business context.")
