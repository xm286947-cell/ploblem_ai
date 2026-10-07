# Hardware AI Retrieval W2 — Search Projection + OpenSearch Indexer

TASK=HARDWARE-AI-RETRIEVAL-W2-SEARCH-PROJECTION-OPENSEARCH-INDEXER-001
SLICE=W2A_METADATA_STORE_AND_FULL_GENERATION_INDEXER
STATUS=DEVELOPMENT
BASE=432c70352731c599f19eeaba545925aa4a6d3b9b

## Reuse

- existing hardware-knowledge-consumption/v1
- W1 hardware-retrieval-metadata/v1
- W1 hardware-retrieval-index-document/v1
- W0 HardwareSearchAdapter HTTP contract
- existing Persistent Data Root rebuildable plane

No second search client and no second Runtime are introduced.

## W2A storage

Rebuildable SQLite file:

`hardware_retrieval_metadata.db`

It stores:

- generation journal
- generation status
- index generation name
- expected/indexed document counts
- knowledge_id / business_case_id
- formal_object_hash
- canonical metadata JSON
- metadata hash
- active generation marker

This database is Level-C rebuildable data. It is not Formal Knowledge.

## Generation state machine

BUILDING
→ INDEXED
→ SWITCHING
→ ACTIVE

Previous ACTIVE becomes SUPERSEDED after successful activation.

Failure before successful alias switch:

→ FAILED
→ previous active generation remains active

SWITCHING exists explicitly so a future W2C recovery step can reconcile a
process crash between remote alias cutover and local activation recording.

## Full rebuild ordering

1. Validate all projection + metadata inputs.
2. Build every W1 index document in memory.
3. Persist BUILDING generation journal and metadata hashes.
4. Create a new generation index.
5. Upsert all documents.
6. Verify indexed_count == expected_count.
7. Mark INDEXED then SWITCHING.
8. Perform atomic OpenSearch alias switch.
9. Mark the generation ACTIVE.

The alias is never switched after a partial document failure.

## Isolation

FORMAL_KNOWLEDGE_CHANGE=NO
STAGE_A_CHANGE=NO
STAGE_B_CHANGE=NO
UNIFIED_KNOWLEDGE_CONTRACT_CHANGE=NO
CURRENT_CONSUMPTION_CONTRACT_CHANGE=NO
CURRENT_SEARCH_UI_CHANGE=NO
RUNTIME_CORE_CHANGE=NO
CURRENT_FINAL_E2E_CHANGE=NO
VECTOR=NO
EMBEDDING=NO
RAG=NO

## Gate

W2A_METADATA_DB=IN_PROGRESS
W2A_GENERATION_JOURNAL=IN_PROGRESS
W2A_FULL_REBUILD=IN_PROGRESS
W2A_ALIAS_SWITCH_AFTER_FULL_SUCCESS_ONLY=IN_PROGRESS
W2A_PARTIAL_FAILURE_OLD_ACTIVE_PRESERVED=IN_PROGRESS
W2A_TARGETED_CI=PENDING

W2B_ONE_REBUILD_AND_SAFE_FALLBACK=NOT_STARTED
W2C_REBUILD_RECOVERY=NOT_STARTED
