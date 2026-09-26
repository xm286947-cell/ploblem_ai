# KNOWLEDGE_STORAGE_COMPATIBILITY_MATRIX_V1.0

| Consumer | Release | Object | Query | Common Evidence | Storage Consumer | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Storage RC1 | KP-STORAGE-RC1-VALIDATION-001 | knowledge-object/v1 | knowledge-query/v1 | common-evidence/v1.0 | UKCI-01/V1.0 | COMPATIBLE |

The Storage consumer validates every row at startup/status/query time.  Missing
binding, missing release, version mismatch, hash mismatch, unsupported
contract, or a `latest` value returns a fail-closed error and no knowledge
results.
