# KNOWLEDGE_RELEASE_UPGRADE_ROLLBACK_POLICY_V1.0

Status: **FROZEN** (RCM-R5)

- Upgrade: publish a new immutable release, create a new binding manifest,
  validate all contract versions and snapshot hashes, run the Storage consumer
  gate, then deploy the binding and release together.
- Rollback: restore the previous binding and its immutable release directory;
  do not mutate a release in place.
- Failure: missing or mismatched binding/release/contract fails closed.  No
  fallback to `latest`, live candidates, unpublished knowledge, or Knowledge
  Production storage is permitted.
- Automatic drift: prohibited.  Storage RC1 has `allow_latest: false` and must
  use an explicit pinned release.
