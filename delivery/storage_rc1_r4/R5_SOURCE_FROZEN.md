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
