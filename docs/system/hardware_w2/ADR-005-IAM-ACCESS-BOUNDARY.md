# ADR-005 IAM / Access Boundary
STATUS=ACCEPTED

Decision:
- Business roles remain CONSUMER and MAINTAINER.
- Host/platform authentication supplies trusted identity/roles in production.
- Existing role header remains internal-test compatibility only.
- Public consumer boundary is read-only CONSUMER.
- Source upload, intake processing, review, mapping, tree import and publish require MAINTAINER.
- API boundary authorizes; domain layer still enforces business invariants.
