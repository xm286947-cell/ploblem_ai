# ADR-004 Data Reliability: Migration, Backup and Restore
STATUS=ACCEPTED

Decision:
- Introduce explicit Hardware schema version metadata.
- Apply ordered transactional forward migrations.
- Schema-changing startup requires verified pre-migration backup.
- Rollback is restore-based.
- Backup manifest binds DB SHA256, schema version, source-root manifest and source checksums.
- Restore runs integrity_check, schema-version verification and source consistency checks before activation.
- Any migration/restore uncertainty blocks startup.
