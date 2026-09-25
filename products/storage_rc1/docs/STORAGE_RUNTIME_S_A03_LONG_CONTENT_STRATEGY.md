# Storage Runtime S-A03 — eMMC Long Content Domain Strategy

Status: PASS
Date: 2026-09-20
Strategy ref: `storage_emmc_field_groups@1`

## Goal

Freeze Storage-owned SourceRef / LogicalUnit / AtomicGroup / Coverage Universe /
Business Merger / Business Completeness Gate semantics without changing Runtime Core.

## Strategy

Current eMMC E2E-01 is primarily an **output-length** risk.  The first production
strategy therefore keeps the frozen full structured source as shared context and splits
the output target by linked field groups.  It does not pretend to solve arbitrarily large
input documents; source-side segmentation can be added later as a separate Domain Strategy
when real input context evidence requires it.

### Coverage Universe

Coverage type: ITEM
Required universe: all 37 frozen eMMC `field_key` values.

A field is technically processed only when a complete structured object for that target
field has been produced and committed.  Chunk count is never used as business completion.

### LogicalUnit

One LogicalUnit per target `field_key`:

`field:<field_key>`

Each unit references the same frozen SourceRef and shared source text, plus the eMMC Schema
context.  Target field semantics remain Storage-owned.

### AtomicGroup / KEEP_TOGETHER

Six groups, maximum size 8:

1. document_identity (8)
2. media_endurance (4)
3. partition_modes (8)
4. standard_health (5)
5. vendor_health (5)
6. management_reliability (7)

Runtime may plan/chunk/retry these groups but must never split a KEEP_TOGETHER group.

### Business Merger

- canonical key = `field_key`
- output ordering = frozen 37-field order
- equivalent duplicate -> dedup + evidence union
- semantic disagreement -> explicit `conflict`; never silently prefer found/missing/source
- missing technical coverage is NOT converted into business `status=missing`
- no facts are fabricated to reach 37/37

### Business Completeness / Review Gate

Business consumable requires:

- all 37 field keys represented;
- legal status found/missing/ambiguous/conflict;
- found/ambiguous/conflict facts have resolved Evidence;
- technical merge complete;
- ambiguous/conflict has completed business review;
- source identity remains stable.

Legitimate `missing` is allowed and does not by itself fail the gate.

## Runtime / Storage boundary

Runtime:
execution, planning, retry, budget, checkpoint, partial commit, coverage accounting,
resume, evidence lineage.

Storage:
field universe, linked groups, business merge, dedup/conflict semantics, ordering,
field-level Evidence rules and Business Gate.
