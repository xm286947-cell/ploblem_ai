# Overall R2 W2 Major Case Governed Production Contract V1

TASK=R2-W2-MAJOR-CASE-GOVERNED-PRODUCTION-AND-REPEAT-CLOSURE-001
ISSUE=#268
STATUS=IMPLEMENTED_FOR_W2_GATE

## Targeted Verification

Existing capabilities are reused:
- ExcelParser + field_mapping remains the only Excel parser/mapping implementation.
- MajorKnowledgeRepository remains the only Major Case master/store.
- Existing Source Fact, Event, Runtime analysis, Human Review, Publisher and historical-case/v1 consumers remain authoritative.
- Repeat Risk consumes only formally published Historical Case artifacts.

W2 closes governance gaps only.

## Governed Excel Intake

CONTRACT=major-excel-import-governance/v1
TEMPLATE_CONTRACT=major-excel-template/v1
TEMPLATE_VERSION=1.0
MAPPING_CONTRACT=major-excel-field-mapping/v1

The official downloadable workbook embeds template and mapping metadata.
Historical unversioned spreadsheets remain LEGACY_COMPATIBLE.
An explicitly versioned unsupported template fails fast.

Preview freezes:
- template_version
- template_status
- mapping_version
- source_sha256
- preview_sha256
- preview_actor

Confirm creates a unique run_id and records actor, timestamps and final counts.

If mapping changes after Preview, Confirm fails closed and the user must Preview again.

## Atomic Import

A batch is all-or-nothing for new W2 confirms.

Before mutation, Confirm rejects:
- non-importable rows
- ambiguous report matches
- missing matched reports
- identity conflicts

During mutation, Major DB and attachment state are snapshotted.
Any unexpected write failure restores both before the batch is marked FAILED.

PARTIAL_VALID_CASE=NO for W2 governed confirms.

Historical PARTIAL rows remain readable for backward compatibility only.

## Source Fact Convergence

Excel batch rows and single document intake both create versioned
kb_source_fact_revision records in the existing Major store.

No second Case, Problem or Source Fact master is introduced.

## Production Continuation

Source Fact
→ existing Unified Runtime analysis
→ AI PENDING candidates
→ explicit Human Review / CONFIRMED revision
→ existing Publisher
→ Historical Case
→ historical-case/v1
→ Repeat Risk

Review and Publish are not bypassed.

## Compatibility

W1 canonical-problem/v1 is unchanged.
Common Evidence contract is unchanged.
No new Web host/port is introduced.

NEXT=W2_FOCUSED_REGRESSION
