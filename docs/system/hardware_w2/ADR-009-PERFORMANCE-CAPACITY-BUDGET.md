# ADR-009 Performance and Capacity Budget
STATUS=ACCEPTED

These are W3 engineering gates, not customer SLA.

Reference budgets:
- startup/readiness <= 30 s excluding dependency installation;
- Search/Detail/Tree public reads p95 <= 1.0 s;
- local review/mapping/publish-gate p95 <= 2.0 s excluding external dependencies;
- Runtime structuring bounded by current 180 s agent timeout;
- capacity envelope: 10k cases, 100k evidence records, 50k tree nodes, 10 concurrent interactive users;
- >20% focused-read regression from recorded baseline requires architecture review.

W3.3 must provide reproducible benchmark fixtures and report environment/data scale with results.
