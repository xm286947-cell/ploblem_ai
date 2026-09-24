# STORAGE-RC1-R5-LAUNCHER-PORT-OWNERSHIP-FIX-001

Status: SOURCE_FROZEN_FOR_BUILD

Frozen input:
- R4A package: STORAGE_PRODUCT_MVP_RC1_DEFECT_115_FIX_CANDIDATE_20260924_R4A.zip
- R4A SHA256: 4de6586bc23a1105e4004faf949897a595a9c38b5382cb3e99d75c15111ddb60
- R4A status: FAILED_RETIRED

R5 scope:
- fail-fast on occupied Storage Web / OpenAI Mock / Mock Router ports;
- reject health responses not owned by the spawned PID;
- preserve configurable STORAGE_WEB_PORT;
- execute TEST-PORT-01 through TEST-PORT-05 in selfcheck;
- preserve Runtime and Knowledge semantics unchanged.

Release ceiling: READY_FOR_PLATFORM_RETEST
Windows platform status for this execution: BLOCKED_NO_WINDOWS_ENVIRONMENT

This commit is the immutable repository source reference embedded into the R5 release manifest.

Preflight note:
- The first internal R5 preflight package SHA d911e81318b4c87591b5c71083c7b8b3829c984362190da040762fb94ecf456a was NOT released.
- Its Launcher code was not the failure; package_contract_gate.py incorrectly required the PORT_CONFLICT literal in run_product_test.sh instead of validating that diagnostic in scripts/port_guard.py.
- The gate assertion has been corrected without changing Runtime/Knowledge semantics.
- This revision supersedes the earlier source-freeze marker for the final R5 build.

Second preflight note:
- Internal preflight SHA 8181a8360b72ef4f2fb1ba1a26f6648870d6583871e429555e2acdcbabcdaa5f was NOT released.
- TEST-PORT-01/02/03/05 passed and normal TEST-PORT-04 passed.
- The next selfcheck scenario exposed a launcher lifecycle bug: cleanup killed owned background PIDs but did not wait for process termination, so port 8765 could still be listening when the next scenario started.
- R5 now owns shutdown as well as startup: every launcher-owned PID is killed and waited before launcher exit.
- This revision supersedes all earlier R5 preflight source markers for final package construction.

macOS native preflight note:
- Internal R5 SHA afe58eed6edd2707f23f931853209f4afaa2a069c61e1a4421c0d012f40b5eae was NOT released.
- Linux package/port/selfcheck gate passed, but macOS native timed out waiting for the OpenAI Mock listener.
- Evidence showed python -m tools.openai_mock.server emitted a runpy warning because tools.openai_mock.__init__ imports server before module execution; the spawned process remained alive but never emitted its listening line in the gate window.
- R5 now invokes the same Mock server through a single import and direct server.main() call. Runtime/Knowledge semantics and Mock API behavior are unchanged.
- This revision supersedes all earlier R5 preflight source markers for final package construction.

Third platform preflight note:
- Linux R5 gate passed with package contract, dual fresh-extract, no-chmod launcher, TEST-PORT-01..05, selfcheck, and regression.
- macOS normal free-port launcher also passed end-to-end with PID ownership verified for OpenAI Mock, Mock Router, and Storage Web.
- macOS then exposed an OS-specific conflict-detection gap in TEST-PORT-01: an existing listener bound to 127.0.0.1:8765 could coexist with a SO_REUSEADDR bind probe against 0.0.0.0:8765, so bind-only preflight was not portable enough.
- R5 port preflight now detects an already-listening endpoint first, then performs the bind probe. Any listener or bind conflict returns PORT_CONFLICT before Product E2E.
- Package Contract now requires this cross-platform listener-detection rule.
- This revision supersedes all prior R5 preflight source markers for final package construction.

