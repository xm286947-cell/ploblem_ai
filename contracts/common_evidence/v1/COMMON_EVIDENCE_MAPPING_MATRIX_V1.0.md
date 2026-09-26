# COMMON_EVIDENCE_MAPPING_MATRIX_V1.0

All rows map producer-owned objects into `common-evidence/v1.0`; no row requires the producer to change its storage model.

| Producer domain | Domain-native Evidence | Common mapping | Missing-value rule |
|---|---|---|---|
| External Source | `EvidenceReference.source`, `.locator`, `.excerpt` | `source.*` from SourceRef; `locator.*` from EvidenceLocator; excerpt/hash/reference preserved | unavailable page/section/URL/hash → `null` |
| Business Object | business `evidence_refs` plus source identity | `producer_domain=Business Object`; source identity and object/version are explicit | missing business version → `null`, never `latest` |
| Historical Case | Historical Case Evidence / `case_id` and source locator | `producer_domain=Historical Case`; `producer_object_id=case_id`; domain adapter maps fact locator | missing fact locator → nullable locator fields |
| Major Issue | issue Evidence / report block reference | `producer_domain=Major Issue`; report source and issue object identity are preserved | no guessed report page or block |
| Hardware Case | hardware Evidence `source_ref`, section/paragraph/block locator | `producer_domain=Hardware Case`; object id/version and evidence status preserved | paragraph/block is anchor only; page remains `null` unless explicit |
| Quality Scenario | scenario assertion / fixture Evidence | `producer_domain=Quality Scenario`; scenario id/version is producer object identity | synthetic fixture source is marked by source type; no production provenance implied |

Common adapter precedence is explicit: domain-native `source_id/source_version` → source object identity; native locator → `page/section/anchor`; native excerpt/text → `excerpt/source_text`; native hash/reference → corresponding field. No filesystem path, SQLite row id, vector id, or index id is a valid Common Evidence identity.
