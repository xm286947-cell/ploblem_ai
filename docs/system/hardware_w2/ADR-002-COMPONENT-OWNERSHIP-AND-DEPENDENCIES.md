# ADR-002 Component Ownership and Dependencies
STATUS=ACCEPTED

Decision:
- Hardware owns domain model, private DB, source registry, tree import, UI/API and Hardware adapters.
- Unified Runtime owns execution/provider/retry/checkpoint/secret execution.
- Unified Knowledge owns generic knowledge production/query mechanics.
- Overall owns only shell/integration consumption.
- CROSS_DOMAIN_SQL, SECOND_RUNTIME and SECOND_KNOWLEDGE_PLATFORM are forbidden.

Dependency direction:
Overall -> Hardware Public Contract
Hardware -> Runtime Public Boundary
Hardware -> Knowledge Public Boundary
No reverse implementation dependency.
