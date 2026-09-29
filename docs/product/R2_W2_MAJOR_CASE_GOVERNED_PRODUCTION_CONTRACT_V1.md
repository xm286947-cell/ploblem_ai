# R2 W2 Major Case Governed Production Contract V1

TASK=R2-W2-MAJOR-CASE-GOVERNED-PRODUCTION-AND-REPEAT-CLOSURE-001
ISSUE=#268
STATUS=IMPLEMENTED_FOR_FOCUSED_GATE

## Direct reuse boundary

The following existing real capabilities remain authoritative and are not
reimplemented by W2:

- existing /p0/major-production page;
- parser.ExcelParser and field_mapping;
- MajorKnowledgeRepository as the only Major Case store;
- existing Source Fact/Event production semantics;
- existing Unified Runtime analysis;
- existing Human Review / confirmed revision;
- existing Major Publisher;
- historical-case/v1;
- existing Repeat Risk consumer.

No second page, parser, workbench, Case master, Problem master, Runtime or
Publish path is introduced.

## Net-new W2 governance

CONTRACT=major-excel-import-governance/v1
TEMPLATE_CONTRACT=major-excel-template/v1
TEMPLATE_VERSION=1.0
MAPPING_CONTRACT=major-excel-field-mapping/v1

The official template embeds template and mapping metadata. Historical
unversioned spreadsheets remain LEGACY_COMPATIBLE. Explicitly versioned but
unsupported templates fail fast.

Preview freezes template_version, mapping_version, source_sha256,
preview_sha256 and preview_actor. Confirm creates a run_id and records actor,
timestamps and imported/rejected/failed results.

If mapping changes after Preview, Confirm fails closed and requires a new
Preview.

## Atomic fail-closed

New governed confirms are all-or-nothing.

Before mutation, the adapter rejects non-importable rows, ambiguous report
matches, missing matched reports and identity conflicts.

During mutation the existing Major DB and attachment state are snapshotted.
Unexpected write failure restores both before the batch/run is recorded
FAILED.

PARTIAL_VALID_CASE=NO for new W2 governed confirms.
The historical PARTIAL status remains readable only for backward
compatibility.

## Source Fact convergence

Both supported intake modes persist into the same existing
`kb_source_fact_revision` model before downstream analysis:

- Excel Batch → `source_type=EXCEL`
- Single Major Source → `source_type=DOCUMENT`

This is convergence on the existing Source Fact store, not a second master.
Excel keeps its frozen idempotence/hash semantics.

## Existing production continuation

Excel Source Fact
→ existing AI Analysis
→ existing Human Review
→ existing Publish
→ Historical Case
→ historical-case/v1
→ existing Repeat Risk

W1 canonical-problem/v1, Common Evidence, Unified Runtime, Provider Stack and
Agent Config are unchanged.


## Single / Batch Source Fact convergence

SINGLE_BATCH_CONVERGENCE=PASS_BY_DESIGN

Both supported production inputs now persist through the same existing
`kb_source_fact_revision` authority:

- Excel Batch → source_type=EXCEL
- Single Major Source Intake → source_type=DOCUMENT

The repository owns the shared Source Fact revision API. Excel preserves its
existing hash/idempotence semantics; document intake receives the same
versioned Source Fact identity without introducing a second Source Fact, Case
or Problem master.

The downstream path remains unchanged:

Source Fact → AI Analysis → Human Review → Publish → Historical Case → Repeat Risk.
