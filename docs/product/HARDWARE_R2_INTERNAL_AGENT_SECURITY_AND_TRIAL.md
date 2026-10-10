# Hardware R2 — Trusted Agent execution and internal trial (NON_PROD only)

This document belongs to the stacked, unmerged R2 S2 PR (#597). It does **not** authorize Real Provider execution or imply native Windows/browser acceptance.

## Current safety boundary

The two user-facing case and Formal knowledge search pages use deterministic public/read-only search. Public GET endpoints never instantiate an online paid Agent. The agent capability is available **only** to an authenticated trusted server-side caller; do not put internal tokens in HTML, JavaScript, localStorage, URLs, client-side environment variables, or screenshots.

- Public fallback: `GET /api/hardware-query/v1/search`. Existing public Formal `/api/public/hardware-knowledge/v1/search` keeps its original contract.
- Query Agent: `POST /api/hardware-query/v1/search-assisted`, header `X-Hardware-Query-Token` (server-to-server only).
- Engineering consumption Agent: `POST /api/hardware-query/v1/analyze`, header `X-Hardware-Analysis-Token` (server-to-server only).
- Both POSTs are **off by default**, require server-side internal tokens, and require all three explicit deployment/feature flags before Provider invocation.

## Query Agent gates

`HARDWARE_R2_DEPLOYMENT_MODE=NON_PROD`, `HARDWARE_QUERY_AGENT_ENABLED=1`,
`HARDWARE_QUERY_AGENT_NONPROD=1`, server-only `HARDWARE_QUERY_AGENT_INTERNAL_TOKEN` and a valid authorized Model Profile/Provider. Missing one gate must yield a blocked response without constructing the Provider. A logged-in end-user session/BFF is **not implemented** yet; no browser UI must carry the internal token.

## Engineering Agent gates

`HARDWARE_R2_DEPLOYMENT_MODE=NON_PROD`, `HARDWARE_CONSUMPTION_AGENT_ENABLED=1`,
`HARDWARE_CONSUMPTION_AGENT_NONPROD=1`, server-only `HARDWARE_ANALYSIS_INTERNAL_TOKEN` and a valid authorized Model Profile/Provider. A valid internal token alone, including in production mode, must **never** enable paid Agent calls.

## Grounding and validation

Search results can reference only actually accessible Formal knowledge IDs (A0152/A0207 are the initial limited known corpus), and must retain Evidence→original Word traceability. Engineering consumption selects/verifies existing Formal field excerpts; agent-suggested free text is not accepted as a verified engineering rule. `evidence_binding=CASE_LEVEL_ONLY` is not field-level proof.

## CI and independent real Gate

Run the focused R2 tests on the exact branch SHA:
```sh
python -m pytest -q tests/test_hardware_r2_query_assist.py tests/test_hardware_r2_query_trusted_gate.py tests/test_hardware_r2_engineering_consumption.py
```

For independent acceptance, in an **approved isolated NON_PROD** environment: record DUT commit / source snapshot hashes, execute positive and negative A0152/A0207 queries in both public UI entry points, test trusted POST from server-side client with separately provisioned secrets, capture genuine Runtime Task/Run/Provider traces, verify fallback when disabled/timeout, and audit matched Knowledge/Evidence IDs and downloaded Word hashes. Do not report MOCK Runtime trace as Real Provider evidence. User-browser Agent invocation additionally requires a reviewed authenticated-session/CSRF-aware BFF. `/ready=503` or missing Model Profile must be reported as BLOCKED, never bypassed.

Related: #584 (real-browser failure), #586 (S1), #587 (S2), #559 (TSE). PR #596, #597, #598 remain separate, unmerged review Gates.
