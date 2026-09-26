# KNOWLEDGE_RELEASE_VERSION_BINDING_V1.0

Status: **FROZEN** after `COMMON_EVIDENCE_CONTRACT_V1.0` passed.

Storage RC1 is bound to one explicit immutable release. The release class is
`CONTROLLED_CONSUMER_VALIDATION`; it is not an AI production release.

| Binding | Pinned value |
|---|---|
| Storage product | `STORAGE_PRODUCT_MVP_RC1` |
| Knowledge release | `KP-STORAGE-RC1-VALIDATION-001` |
| Knowledge Object | `knowledge-object/v1` |
| Knowledge Query | `knowledge-query/v1` |
| Common Evidence | `common-evidence/v1.0` |
| Storage consumer | `UKCI-01/V1.0` |
| Qualification state | `CONTROLLED_CONSUMER_VALIDATION` |

The machine-readable binding is `release_binding.json` in this directory.
Storage must send the pinned `knowledge_release_version` on every query. It
must not resolve `latest`, live Candidates, unpublished Knowledge, or a
producer database.
