# QUALITY KNOWLEDGE M4 DELIVERY

Status: Released
Scope: Existing Capability Integration

## Delivered
- KnowledgeService extended with Quality Issue read/analysis/retrieval publishing APIs.
- QualityKnowledgeRetrievalAdapter maps SQLite Quality Issue Knowledge into the existing retrieval_document contract.
- Compatibility source artifacts are published without modifying existing standard_case/enriched_case assets.
- Existing Repeat Case Similarity / Solution Analysis / Repeat Decision code is unchanged.
- Publishing is idempotent by retrieval content hash and supports overwrite.
- CLI: `publish-quality-knowledge-retrieval` supports one issue or filtered business batch.

## Boundary
Quality Issue SQLite remains behind KnowledgeService/repository boundaries. Repeat Case consumes the same retrieval contract as before; M4 adds an adapter rather than a second retrieval engine.
