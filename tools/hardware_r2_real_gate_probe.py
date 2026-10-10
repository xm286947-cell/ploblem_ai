"""Hardware R2 real-data gate probe.

Read-only by default; online Agent calls are explicit, local, NON_PROD only.
This is a server-side diagnostic, NOT a replacement for native Chrome/TSE UAT.
No token, prompt, or user-provided knowledge content is printed or persisted.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

QUERIES = (
    ("模拟量", "A0207"),
    ("ADC参考源", "A0207"),
    ("模拟量偏差", "A0207"),
    ("串口乱码", "A0152"),
    ("MCU", "A0152"),
    ("A0207", "A0207"),
    ("设计模拟量电路时，有什么经验可以借鉴？", "A0207"),
    ("复位问题", None),
)
LONG_QUESTION = "设计模拟量电路时，有什么经验可以借鉴？"
AGENT_FLAGS = (
    "HARDWARE_R2_DEPLOYMENT_MODE",
    "HARDWARE_QUERY_AGENT_ENABLED",
    "HARDWARE_QUERY_AGENT_NONPROD",
    "HARDWARE_CONSUMPTION_AGENT_ENABLED",
    "HARDWARE_CONSUMPTION_AGENT_NONPROD",
)
TOKENS = ("HARDWARE_QUERY_AGENT_INTERNAL_TOKEN", "HARDWARE_ANALYSIS_INTERNAL_TOKEN")


class ProbeError(RuntimeError):
    pass


def validate_local_base(base_url: str) -> str:
    parsed = urlsplit(base_url.strip())
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ProbeError("LOCAL_HTTP_ONLY")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ProbeError("INVALID_BASE_URL")
    return base_url.rstrip("/")


def ensure_real_agent_permission(env: dict[str, str], *, explicit: bool) -> None:
    """Fail before network access or reading any token unless authorized."""
    if not explicit:
        raise ProbeError("EXPLICIT_REAL_PROVIDER_AUTHORIZATION_REQUIRED")
    expected = {
        "HARDWARE_R2_DEPLOYMENT_MODE": "NON_PROD",
        "HARDWARE_QUERY_AGENT_ENABLED": "1",
        "HARDWARE_QUERY_AGENT_NONPROD": "1",
        "HARDWARE_CONSUMPTION_AGENT_ENABLED": "1",
        "HARDWARE_CONSUMPTION_AGENT_NONPROD": "1",
    }
    if any(env.get(name) != value for name, value in expected.items()):
        raise ProbeError("NONPROD_AGENT_FLAGS_REQUIRED")
    if any(not env.get(name) for name in TOKENS):
        raise ProbeError("SERVER_SIDE_AGENT_TOKENS_REQUIRED")


def _request(base: str, method: str, path: str, *, payload: dict[str, Any] | None = None,
             token: str | None = None, token_header: str | None = None, timeout: float = 30.0) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token is not None:
        if not token_header:
            raise ProbeError("TOKEN_HEADER_REQUIRED")
        headers[token_header] = token
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(base + path, method=method, data=data, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as response:
            body = response.read(4 * 1024 * 1024)
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ProbeError("HTTP_RESPONSE_NOT_OBJECT")
            return result
    except HTTPError as exc:
        # Do not echo response bodies: remote error responses can contain sensitive material.
        raise ProbeError("HTTP_STATUS_" + str(exc.code)) from None
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise ProbeError("HTTP_OR_JSON_FAILURE_" + type(exc).__name__) from None


def _case_ids(response: dict[str, Any]) -> list[str]:
    rows = response.get("results")
    if not isinstance(rows, list):
        raise ProbeError("SEARCH_RESULTS_INVALID")
    return [str(row.get("business_case_id", "")) for row in rows if isinstance(row, dict)]


def _trace_info(response: dict[str, Any], *, analysis: bool) -> dict[str, Any]:
    if analysis:
        raw = response.get("trace")
    else:
        raw = (response.get("retrieval") or {}).get("query_agent", {}).get("trace")
    if not isinstance(raw, dict):
        raise ProbeError("REAL_AGENT_TRACE_ABSENT")
    # Never echo raw trace; reduce to auditable and non-sensitive identifiers only.
    safe = {key: raw.get(key) for key in ("agent_id", "task_id", "run_id", "provider",
                                         "model", "provider_calls", "duration_ms", "trace_id")}
    if not safe["task_id"] or not safe["run_id"] or int(safe["provider_calls"] or 0) < 1:
        raise ProbeError("REAL_AGENT_PROVIDER_TRACE_INCOMPLETE")
    provider = str(safe["provider"] or "").strip()
    model = str(safe["model"] or "").strip()
    if not provider or not model or any(
        marker in (provider + " " + model).lower()
        for marker in ("mock", "fake", "dummy", "test-only")
    ):
        raise ProbeError("REAL_AGENT_PROVIDER_IDENTITY_UNVERIFIED")
    return safe


def _get_formal_object(base: str, knowledge_id: str) -> dict[str, Any]:
    from urllib.parse import quote
    if not knowledge_id:
        raise ProbeError("FORMAL_KNOWLEDGE_ID_MISSING")
    return _request(base, "GET", "/api/public/hardware-knowledge/v1/objects/" + quote(knowledge_id, safe=""))


def probe(base_url: str, *, real_agent: bool = False, explicit: bool = False,
          environ: dict[str, str] | None = None) -> dict[str, Any]:
    base = validate_local_base(base_url)
    env = dict(os.environ if environ is None else environ)
    # Validate before *any* network action when paid Agent execution is requested.
    if real_agent:
        ensure_real_agent_permission(env, explicit=explicit)

    rows: list[dict[str, Any]] = []
    formal_by_case: dict[str, str] = {}
    for question, expected_id in QUERIES:
        response = _request(base, "GET", "/api/hardware-query/v1/search?" + urlencode({"text": question, "limit": 20}))
        actual = _case_ids(response)
        # Top-5 acceptance must not silently succeed on a sixth or later hit.
        ok = expected_id in actual[:5] if expected_id is not None else len(actual) == 0
        if expected_id in {"A0152", "A0207"} and expected_id not in formal_by_case:
            matches = [row for row in response.get("results", []) if isinstance(row, dict)
                       and row.get("business_case_id") == expected_id]
            if matches and matches[0].get("knowledge_id"):
                formal_by_case[expected_id] = str(matches[0]["knowledge_id"])
        rows.append({"query": question, "expected": expected_id or "NO_HIT",
                     "actual_business_case_ids": actual[:5], "hit_count": len(actual), "pass": ok})
    report: dict[str, Any] = {
        "scope": "LOCAL_NONPROD_READ_ONLY" if not real_agent else "LOCAL_NONPROD_AUTHORIZED_PROVIDER",
        "deterministic_query_results": rows,
        "deterministic_gate": "PASS" if all(row["pass"] for row in rows) else "FAIL",
        "online_agent_gate": "NOT_RUN",
        "engineering_agent_gate": "NOT_RUN",
        "native_chrome_gate": "NOT_RUN",
        "original_word_sha_gate": "NOT_RUN",
        "independent_tse_gate": "NOT_RUN",
    }
    # A matching case ID alone is not acceptable: both sources must resolve to
    # actual Formal objects with existing case-level Evidence references.
    identities: dict[str, Any] = {}
    for case_id in ("A0152", "A0207"):
        knowledge_id = formal_by_case.get(case_id)
        if not knowledge_id:
            raise ProbeError("FORMAL_KNOWLEDGE_ID_MISSING_" + case_id)
        obj = _get_formal_object(base, knowledge_id)
        if obj.get("knowledge_id") != knowledge_id or obj.get("business_case_id") != case_id:
            raise ProbeError("FORMAL_IDENTITY_MISMATCH_" + case_id)
        evidence_refs = obj.get("evidence_refs")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            raise ProbeError("FORMAL_CASE_EVIDENCE_MISSING_" + case_id)
        identities[case_id] = {
            "knowledge_id": knowledge_id, "business_case_id": case_id,
            "evidence_ref_count": len(evidence_refs),
        }
    report["formal_identity"] = identities
    report["a0207_formal_identity"] = identities["A0207"]

    if not real_agent:
        return report

    query = _request(base, "POST", "/api/hardware-query/v1/search-assisted",
                     payload={"text": LONG_QUESTION, "limit": 20},
                     token=env[TOKENS[0]], token_header="X-Hardware-Query-Token")
    if "A0207" not in _case_ids(query):
        raise ProbeError("REAL_QUERY_AGENT_A0207_NOT_RECALLED")
    report["query_agent_trace"] = _trace_info(query, analysis=False)
    report["online_agent_gate"] = "PASS"

    knowledge_id = next((row.get("knowledge_id") for row in query.get("results", [])
                         if isinstance(row, dict) and row.get("business_case_id") == "A0207"), "")
    obj = _get_formal_object(base, str(knowledge_id))
    analysis = _request(base, "POST", "/api/hardware-query/v1/analyze",
                        payload={"knowledge_id": knowledge_id, "task_intent": "DESIGN_REUSE"},
                        token=env[TOKENS[1]], token_header="X-Hardware-Analysis-Token")
    if analysis.get("knowledge_id") != knowledge_id or analysis.get("evidence_binding") != "CASE_LEVEL_ONLY":
        raise ProbeError("ENGINEERING_ANALYSIS_IDENTITY_OR_EVIDENCE_SCOPE_INVALID")
    items = analysis.get("items")
    if not isinstance(items, list) or not items:
        raise ProbeError("ENGINEERING_ANALYSIS_EMPTY")
    for item in items:
        if not isinstance(item, dict) or item.get("advice_scope") != "VERBATIM_FORMAL_EXCERPT":
            raise ProbeError("ENGINEERING_ANALYSIS_UNGROUNDED_OUTPUT")
        excerpt = item.get("source_quote")
        field = item.get("source_field")
        if not isinstance(excerpt, str) or not isinstance(field, str) or excerpt not in str(obj.get(field) or ""):
            raise ProbeError("ENGINEERING_ANALYSIS_QUOTE_NOT_IN_FORMAL")
        if item.get("suggestion") != excerpt:
            raise ProbeError("ENGINEERING_ANALYSIS_NOT_VERBATIM")
    report["engineering_agent_trace"] = _trace_info(analysis, analysis=True)
    report["engineering_agent_gate"] = "PASS"
    report["engineering_item_count"] = len(items)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hardware R2 localhost real-data gate")
    parser.add_argument("--base-url", default="http://127.0.0.1:18785")
    parser.add_argument("--real-agent", action="store_true", help="Call two billable Agents in authorized NON_PROD")
    parser.add_argument("--authorize-provider-call", action="store_true",
                        help="Explicit approval of the real Query + Engineering Agent calls")
    args = parser.parse_args(argv)
    try:
        result = probe(args.base_url, real_agent=args.real_agent,
                       explicit=args.authorize_provider_call)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["deterministic_gate"] != "PASS":
            return 2
        return 0
    except ProbeError as error:
        print("RESULT=BLOCKED_OR_FAILED", file=sys.stderr)
        print("ERROR_CODE=" + str(error), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
