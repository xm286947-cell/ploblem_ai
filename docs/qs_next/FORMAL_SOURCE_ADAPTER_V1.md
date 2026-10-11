# QS Next — Formal Source Adapter V1 (Isolated Candidate)

**Status:** IMPLEMENTED_CONTRACT_ONLY / NOT_PRODUCT_CONNECTED / NOT_UAT_PASSED
**Branch:** `feature/qs-next-formal-source-adapter-20261011`
**Base:** `main@de8bbd6de1513b9bdceb9a0336b163d5595244cb`
**Frozen legacy identity:** `2026-09-13 PATCH57@6bd207e00b38e3688c817f27621bfedf052aeffb`

## Purpose

Normalize the two **formal** production sources for new Quality Scenarios:

- `THOROUGH_SOLUTION_ORDER` — 彻底解决单
- `MISSED_TEST_ANALYSIS` — 漏测分析

`ITR` / source-problem identity is optional relation/trace context only. It
**cannot** be passed as a formal production source.

This implementation does not import or mutate the restored PATCH57 scenario
repository, its SQLite files, original business materials, runtime pages, or
the ongoing macOS UAT. Neither `main` nor PR #610 is changed or merged.

## Standalone input contract

```python
from quality_knowledge.qs_next_formal_sources import normalize_formal_sources

bundle = normalize_formal_sources([{
    "source_type": "THOROUGH_SOLUTION_ORDER",
    "source_record_id": "CS-001",    # original controlled business source ID
    "problem_ref": "ITR-001",         # optional trace context, not source
    "facts": {
        "failure_mode": "设备重启后状态丢失",
        "operating_condition": "持续运行"
    },
    "evidence": [{
        "evidence_id": "CS-001:section4",
        "locator": "source://CS-001/section/4",  # controlled source locator
        "excerpt": "来源原文片段",
        "supports": ["failure_mode"],
        "evidence_kind": "FACT",        # FACT / INFERRED / HUMAN_CONFIRMED
        "confidence": 0.9
    }]
}])
```

The above is a **synthetic example**, not validated original business data.

Output contains the original typed source refs, explicit evidence, facts,
fields lacking factual evidence, `source_coverage`, and optional problem
reference context. `publish_ready=False` and
`status=SOURCE_CONTEXT_ONLY` are always returned; source normalization
must never auto-publish or auto-confirm a Quality Scenario.

### Fail-closed rules

1. Reject unknown production types (including ITR).
2. Reject missing/duplicate source-record IDs, evidence IDs, and cross-source
   evidence refs.
3. Do not infer source relationships from similar text, customer, date or
   product. A multi-record bundle requires the same explicit `problem_ref`.
4. Every evidence reference must contain original text or a locator and state
   the original factual field(s) it supports.
5. `INFERRED` evidence does not close the requirement for factual evidence.
6. Single-source records are valid with `source_coverage=PARTIAL`.
   `FULL` only indicates both source *types* appear in the bundle; it does
   **not** imply full field coverage, validation, review or publication.
7. Missing facts remain missing, never generated.

### Integration and migration gates

This **isolated contract** is intentionally **not connected** to any production
route, database or source reader. Existing functionality therefore stays
unchanged whether this module exists or not.

Next separate increments:

- Read official materials through existing source/knowledge contracts,
  preserving their true source ID, revision, original document evidence and
  authorization boundaries (do **not** assume this test DTO is the original
  raw material schema).
- Map source facts to all five original PATCH57 model dimensions through a
  non-destructive adapter. Reuse existing taxonomy and semantic dictionary.
- Produce one scenario candidate per controlled issue, with unresolved
  fields explicitly flagged for human review.
- Reuse historical scenario assets for **human-approved** multi-problem
  grouping; any smart similarity is recommendation only.
- Consume published assets for P04 product/customer/industry views through
  its public contract.
- Before integration, verify no regression against the September 13 version,
  original macOS application, and permitted real data.

### Acceptance status

- Standalone source normalization: unit/CI validation only.
- Actual original material reader integration: NOT_STARTED.
- AI/provider scenario generation: NOT_STARTED.
- Evidence drilldown in a running product: NOT_STARTED.
- Actual business case replay: NOT_STARTED.
- Legacy regression/native macOS gate: NOT_RUN_FOR_NEW_BRANCH.
- Release/PR merge: NOT_APPROVED.

## Development boundaries

Do not modify `6bd207e...`, the PATCH57 validation installation,
original database, original dictionary/scenario tables, unrelated workbenches,
or PR #610. All new changes require independent feature branches, reviews
and source/evidence-led tests.
