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

## Executable S0/S1/S2 localhost acceptance probe (not a formal TSE gate)

The stacked S2 branch includes `tools/hardware_r2_real_gate_probe.py`, a
stdlib-only local HTTP probe. **Run it on the machine hosting the isolated
approved Formal data**; never run it against the production database or a
public URL. It checks the known A0152/A0207 search cases and the associated
Formal identity. It does not inspect the original Word bytes, run Chrome, or
substitute for TSE/user-native acceptance.

Read-only, no Provider request (default):

```sh
python tools/hardware_r2_real_gate_probe.py --base-url http://127.0.0.1:18785
```

Expected positive probes: `模拟量`, `ADC参考源`, `模拟量偏差`,
`A0207`, long natural-language design question → A0207; `串口乱码`,
`MCU` → A0152. `复位问题` must not invent a hit. An empty or wrong
projection results in `deterministic_gate=FAIL`, not a fake green result.

**Only after independent approval of paid, real Provider execution**, provision
server-side variables securely (do not put credentials in files or shell history)
and separately enable the NON_PROD query/consumption gates. Then run:

```sh
python tools/hardware_r2_real_gate_probe.py --base-url http://127.0.0.1:18785 \
  --real-agent --authorize-provider-call
```

This intentionally attempts **one live query Agent call plus one live
engineering-consumption Agent call** and is blocked unless the environment
also contains both internal tokens and all five NON_PROD flags. The API
independently enforces its own flags and secrets. Do not disable its checks.
It reports only trace IDs/Provider metadata, case IDs, case Evidence count,
and PASS/FAIL; it never logs token values, prompts or full knowledge text.

Passing this utility does **not** prove real Chrome UI Agent use (browser is
currently deterministic and is not authorized to carry internal credentials),
field-level evidence linkage, real engineering-value approval, or final release.
After this probe, TSE must still do original Word SHA, Chrome, Windows, and
independent source-grounding review on the exact test SHA.
