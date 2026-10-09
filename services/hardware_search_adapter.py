"""HTTP adapter for a rebuildable OpenSearch retrieval engine.

This module is deliberately independent from Formal Knowledge, Stage A/B,
Unified Runtime, and operating-system-specific launch logic.  It provides the
small W0 transport boundary required by the AI retrieval architecture.
"""
from __future__ import annotations

import json
import socket
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


DEFAULT_SEARCH_FIELDS: tuple[str, ...] = (
    "title^3",
    "search_text^2",
    "tags",
    "component",
    "interface",
)


class HardwareSearchAdapterError(RuntimeError):
    """Stable adapter error used by callers and future fallback logic."""

    def __init__(self, code: str, *, status: int | None = None):
        self.code = str(code)
        self.status = status
        super().__init__(self.code)


class HardwareSearchAdapter:
    """Minimal OpenSearch HTTP adapter.

    The adapter intentionally knows nothing about Hardware Formal Knowledge.
    Callers pass already-derived search documents and receive raw hit payloads.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 5.0,
        username: str | None = None,
        password: str | None = None,
    ):
        normalized = str(base_url or "").strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise HardwareSearchAdapterError("SEARCH_ENGINE_URL_INVALID")
        if float(timeout_seconds) <= 0:
            raise HardwareSearchAdapterError("SEARCH_ENGINE_TIMEOUT_INVALID")
        self.base_url = normalized
        self.timeout_seconds = float(timeout_seconds)
        self.username = username
        self.password = password

    @staticmethod
    def _json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @staticmethod
    def _error_for_status(status: int) -> str:
        if status in {401, 403}:
            return "SEARCH_ENGINE_AUTH_FAILED"
        if status == 404:
            return "SEARCH_INDEX_NOT_FOUND"
        if status == 408:
            return "SEARCH_ENGINE_TIMEOUT"
        return "SEARCH_ENGINE_REQUEST_FAILED"

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | Sequence[Any] | None = None,
        params: Mapping[str, Any] | None = None,
        allowed_statuses: set[int] | None = None,
    ) -> tuple[int, Any]:
        query = ""
        if params:
            query = "?" + urlencode(
                [(str(key), str(value).lower() if isinstance(value, bool) else str(value))
                 for key, value in params.items()]
            )
        url = f"{self.base_url}/{path.lstrip('/')}{query}"
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = self._json_bytes(body)
            headers["Content-Type"] = "application/json"

        if self.username is not None or self.password is not None:
            if not self.username or self.password is None:
                raise HardwareSearchAdapterError("SEARCH_ENGINE_AUTH_CONFIG_INVALID")
            import base64

            token = base64.b64encode(
                f"{self.username}:{self.password}".encode("utf-8")
            ).decode("ascii")
            headers["Authorization"] = f"Basic {token}"

        request = Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read()
                status = int(response.status)
        except HTTPError as error:
            status = int(error.code)
            if allowed_statuses and status in allowed_statuses:
                payload = error.read()
            else:
                raise HardwareSearchAdapterError(
                    self._error_for_status(status), status=status
                ) from error
        except (TimeoutError, socket.timeout) as error:
            raise HardwareSearchAdapterError("SEARCH_ENGINE_TIMEOUT") from error
        except URLError as error:
            if isinstance(getattr(error, "reason", None), (TimeoutError, socket.timeout)):
                raise HardwareSearchAdapterError("SEARCH_ENGINE_TIMEOUT") from error
            raise HardwareSearchAdapterError("SEARCH_ENGINE_UNAVAILABLE") from error
        except OSError as error:
            raise HardwareSearchAdapterError("SEARCH_ENGINE_UNAVAILABLE") from error

        if payload in (b"", None):
            return status, None
        try:
            return status, json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HardwareSearchAdapterError("SEARCH_ENGINE_RESPONSE_INVALID") from error

    @staticmethod
    def _name(value: str, *, code: str) -> str:
        name = str(value or "").strip()
        if not name or "/" in name or "\\" in name or any(ch.isspace() for ch in name):
            raise HardwareSearchAdapterError(code)
        return name

    def health(self) -> dict[str, Any]:
        status, root = self._request("GET", "/")
        _, cluster = self._request("GET", "/_cluster/health")
        return {
            "reachable": status == 200,
            "version": (root or {}).get("version", {}).get("number"),
            "distribution": (root or {}).get("version", {}).get("distribution"),
            "cluster_name": (root or {}).get("cluster_name"),
            "cluster_status": (cluster or {}).get("status"),
            "number_of_nodes": (cluster or {}).get("number_of_nodes"),
        }

    def ensure_index(
        self,
        index_name: str,
        mapping: Mapping[str, Any],
    ) -> dict[str, Any]:
        index = self._name(index_name, code="SEARCH_INDEX_NAME_INVALID")
        status, _ = self._request(
            "HEAD",
            f"/{quote(index, safe='')}",
            allowed_statuses={404},
        )
        if status == 200:
            return {"index": index, "created": False}
        try:
            _, payload = self._request("PUT", f"/{quote(index, safe='')}", body=mapping)
        except HardwareSearchAdapterError as error:
            if error.code == "SEARCH_ENGINE_REQUEST_FAILED":
                raise HardwareSearchAdapterError(
                    "SEARCH_INDEX_BUILD_FAILED", status=error.status
                ) from error
            raise
        return {
            "index": index,
            "created": bool((payload or {}).get("acknowledged")),
        }

    def upsert(
        self,
        index_name: str,
        document_id: str,
        document: Mapping[str, Any],
        *,
        refresh: bool = True,
    ) -> dict[str, Any]:
        index = self._name(index_name, code="SEARCH_INDEX_NAME_INVALID")
        doc_id = str(document_id or "").strip()
        if not doc_id:
            raise HardwareSearchAdapterError("SEARCH_DOCUMENT_ID_REQUIRED")
        _, payload = self._request(
            "PUT",
            f"/{quote(index, safe='')}/_doc/{quote(doc_id, safe='')}",
            body=document,
            params={"refresh": refresh},
        )
        return {
            "index": index,
            "document_id": doc_id,
            "result": (payload or {}).get("result"),
            "version": (payload or {}).get("_version"),
        }

    def search(
        self,
        index_or_alias: str,
        text: str = "",
        *,
        filters: Mapping[str, Any] | None = None,
        limit: int = 20,
        fields: Sequence[str] = DEFAULT_SEARCH_FIELDS,
    ) -> dict[str, Any]:
        target = self._name(index_or_alias, code="SEARCH_INDEX_NAME_INVALID")
        if int(limit) < 1 or int(limit) > 500:
            raise HardwareSearchAdapterError("SEARCH_LIMIT_INVALID")

        must: list[dict[str, Any]] = []
        query_text = str(text or "").strip()
        if query_text:
            must.append(
                {
                    "multi_match": {
                        "query": query_text,
                        "fields": list(fields),
                        "type": "best_fields",
                        "operator": "and",
                    }
                }
            )
        else:
            must.append({"match_all": {}})

        exact_filters = [
            {"term": {str(name): value}}
            for name, value in (filters or {}).items()
            if value not in (None, "", [])
        ]
        body = {
            "size": int(limit),
            "query": {
                "bool": {
                    "must": must,
                    "filter": exact_filters,
                }
            },
        }
        try:
            _, payload = self._request(
                "POST",
                f"/{quote(target, safe='')}/_search",
                body=body,
            )
        except HardwareSearchAdapterError as error:
            if error.code in {
                "SEARCH_ENGINE_REQUEST_FAILED",
                "SEARCH_INDEX_NOT_FOUND",
            }:
                raise HardwareSearchAdapterError(
                    "SEARCH_QUERY_FAILED", status=error.status
                ) from error
            raise
        hits = list(((payload or {}).get("hits") or {}).get("hits") or [])
        return {
            "index": target,
            "total": ((payload or {}).get("hits") or {}).get("total"),
            "hits": hits,
        }

    def switch_alias(self, alias: str, new_index: str) -> dict[str, Any]:
        alias_name = self._name(alias, code="SEARCH_ALIAS_NAME_INVALID")
        index = self._name(new_index, code="SEARCH_INDEX_NAME_INVALID")
        status, existing = self._request(
            "GET",
            f"/_alias/{quote(alias_name, safe='')}",
            allowed_statuses={404},
        )
        actions: list[dict[str, Any]] = []
        if status == 200 and isinstance(existing, Mapping):
            for current_index in sorted(existing):
                if current_index != index:
                    actions.append(
                        {"remove": {"index": current_index, "alias": alias_name}}
                    )
        actions.append({"add": {"index": index, "alias": alias_name}})
        _, payload = self._request("POST", "/_aliases", body={"actions": actions})
        return {
            "alias": alias_name,
            "index": index,
            "acknowledged": bool((payload or {}).get("acknowledged")),
        }

    def delete_index(self, index_name: str) -> dict[str, Any]:
        index = self._name(index_name, code="SEARCH_INDEX_NAME_INVALID")
        status, payload = self._request(
            "DELETE",
            f"/{quote(index, safe='')}",
            allowed_statuses={404},
        )
        return {
            "index": index,
            "deleted": status != 404 and bool((payload or {}).get("acknowledged")),
            "missing": status == 404,
        }


__all__ = [
    "DEFAULT_SEARCH_FIELDS",
    "HardwareSearchAdapter",
    "HardwareSearchAdapterError",
]
