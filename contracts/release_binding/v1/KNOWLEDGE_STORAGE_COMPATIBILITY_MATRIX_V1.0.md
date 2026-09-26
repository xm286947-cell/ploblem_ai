# KNOWLEDGE_STORAGE_COMPATIBILITY_MATRIX_V1.0

| Producer/consumer artifact | Required version | Gate |
|---|---|---|
| Knowledge Release | `KP-STORAGE-RC1-VALIDATION-001` | immutable manifest exists and hash is valid |
| Published Knowledge Object | `knowledge-object/v1` | every released object validates |
| Knowledge Query | `knowledge-query/v1` | pinned query facade only |
| Common Evidence | `common-evidence/v1.0` | every consumer Evidence maps to v1.0 |
| Storage Consumer | `UKCI-01/V1.0` | request/response adapter remains compatible |
| Storage Product | `STORAGE_PRODUCT_MVP_RC1` | explicit product binding |

An absent row, a version mismatch, or a release manifest that cannot be
validated is incompatible. No row may be satisfied by a `latest` alias.
