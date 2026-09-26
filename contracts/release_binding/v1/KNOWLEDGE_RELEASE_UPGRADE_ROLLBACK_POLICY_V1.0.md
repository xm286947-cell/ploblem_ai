# KNOWLEDGE_RELEASE_UPGRADE_ROLLBACK_POLICY_V1.0

## Startup and fail-closed behavior

1. Storage loads the binding selected for its product version.
2. It requires an explicit `knowledge_release_version`; `latest`, empty, and
   live Candidate references are invalid.
3. It validates the release manifest, Object Contract, Query Contract, Common
   Evidence Contract, and Storage Consumer Contract before serving queries.
4. Missing release, malformed manifest, or any version mismatch fails closed.
   No fallback to a live store or another release is permitted.

## Upgrade

An upgrade creates a new binding with a new release snapshot and runs the full
compatibility gate against that binding. The current binding remains active
until the new gate passes and the product explicitly selects the new binding.

## Rollback

Rollback selects the previously approved binding and its immutable release
version. It does not rebuild or mutate the old snapshot. If the previous
binding is unavailable or fails validation, Storage remains failed closed.

## Floating dependency policy

Storage RC1 is prohibited from consuming `latest`, live Candidates, live
unpublished Knowledge, or a Knowledge database. Automatic drift is `NO`.
