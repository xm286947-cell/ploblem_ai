# ADR-001 Public Contract Deployment and Versioning
STATUS=ACCEPTED

Decision:
- `hardware-public-consumer/v1` is the only cross-product consumer contract.
- Bind it to `/api/public/hardware/v1` on the existing shared FastAPI host.
- Keep existing Hardware product APIs separate.
- Additive changes stay v1; breaking semantics require v2.
- Support v1 for at least one Hardware release after v2 introduction.
- Overall is consumer-only and may not import Hardware internals.

Consequences:
- W3.1 adds only a thin HTTP facade and compatibility tests.
- No new DB, business service, Web host or port.
