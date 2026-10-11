# QS Next — Three Entry Workbenches / Domain Coverage Contract V1

**Status:** IMPLEMENTED_ISOLATED_CONTRACT; not connected to actual pages, original DB, live service, or real case UAT.
**Baseline:** `main@de8bbd6de1513b9bdceb9a0336b163d5595244cb`
**Frozen historic PATCH57:** `6bd207e00b38e3688c817f27621bfedf052aeffb`

## 1. Binding requirement (2026-10-11)

All three mature workbenches are valid ways for a user to **initiate**
Quality Scenario extraction. Their allowed **problem domains** differ:

| Workbench entry | Legacy material type | Allowed problem domains | Counts as a formal scenario-production source? |
| --- | --- | --- | --- |
| `THOROUGH_SOLUTION` (彻底解决工作台) | `ITR_CS` | `SOFTWARE`, `HARDWARE`, `MECHANICAL`, including explicitly multiple domains | **YES** (`THOROUGH_SOLUTION_ORDER`) |
| `MISSED_TEST` (漏测分析工作台) | `ESCAPE_ANALYSIS` | `SOFTWARE` ONLY | **YES** (`MISSED_TEST_ANALYSIS`) |
| `SOFTWARE_ASSESSMENT` (软件问题考核工作台) | `SOFTWARE_OPERATION` | `SOFTWARE` ONLY | **NO** (entry + context only; must resolve a formal source) |

An ITR may be used as a **canonical problem reference**, but is NOT a
fourth entry or a third formal scenario-production source.

This separation is intentional: user navigation from a software assessment
record must work while its KPI/assessment fields remain context; it requires
a verified `ITR_CS` and/or `ESCAPE_ANALYSIS` formal record to extract facts.

Do not confuse `problem domain` (software/hardware/mechanical) with
`product_code` (e.g. PLC/HMI/iFA). A product can have quality scenarios in
multiple domains. The CS entry can carry `domains=["SOFTWARE", "HARDWARE"]`
without duplicating the problem.

### Existing PATCH57 interfaces verified in source

- `/materials/cs/{material_id}` is an existing original material page.
- `/materials/software-operations/{material_id}` is an existing original material page.
- Original `source_material` table has `material_id`, `material_type`,
  `canonical_itr`, `version_no`, `source_hash`, `raw_json`.
- Original `issue_material_link` statuses include `LINKED`,
  `MANUAL_LINKED`; other possible outcomes like `CONFLICT` or
  `ITR_NOT_FOUND` must not qualify as verified relationships.
- Original `related_materials()` lists same-ITR records; **that query alone
  is NOT a verified relation proof** for this contract.
- The missed-test page route is **not assumed** to be `/materials/missed-test`;
  confirm its real navigation route at integration time.

## 2. New read-only entry contract

`quality_knowledge.qs_next_entry_routing.resolve_quality_scenario_entry`
receives an original source record snapshot and explicitly related formal
material snapshots from an authorized caller.

Each record's identity is bound to its immutable `material_id`, positive
`version_no`, and original 64-char `source_hash`; missing identity fails
closed. Cross-record grouping requires `LINKED` or `MANUAL_LINKED`, a
non-empty trusted `relation_ref`, and matching **canonical** problem
reference. Upstream association resolver is responsible for validating the
association (including permissions); this pure function does not query a
database and cannot validate caller-provided values for authenticity.

A software-only entry defaults to `SOFTWARE` due to the controlled
workbench domain, not because an AI guessed from symptoms. An unclassified CS
entry remains `DOMAIN_REVIEW_REQUIRED`; it is NEVER silently converted to
software. An explicit hardware or mechanical domain on either software-only
workbench is rejected. For a related CS report with multiple domains, a
software-only entry can use the software portion, but it is not thereby
promoted to hardware or mechanical.

```python
from quality_knowledge.qs_next_entry_routing import resolve_quality_scenario_entry
plan = resolve_quality_scenario_entry(
    {
        "workbench": "SOFTWARE_ASSESSMENT",
        "material_type": "SOFTWARE_OPERATION",
        "material_id": "MAT-SW-1",
        "version_no": 1,
        "source_hash": "a" * 64,
        "canonical_problem_ref": "ITR-001",
    },
    related_sources=[
        {
            "material_type": "ITR_CS",
            "material_id": "MAT-CS-1",
            "version_no": 2,
            "source_hash": "b" * 64,
            "canonical_problem_ref": "ITR-001",
            "link_status": "LINKED",
            "relation_ref": "LNK-001",
            "domains": ["SOFTWARE"],
        }
    ],
)
```

Example is synthetic. No real business data has been read in this PR.

The `formal_source_reads` output is a **read plan**, not actual extracted
facts or a generated/published scenario.

Status values: `DOMAIN_REVIEW_REQUIRED`,
`FORMAL_SOURCE_REQUIRED`, `READY_FOR_SOURCE_READ`.
A `FULL` source-coverage label only says that both CS and missed-test source
types were linked; it does not indicate complete field evidence or UAT PASS.

## 3. Incremental integration plan

1. Maintain independent draft PR #621's typed formal-source normalization
   and this PR's three-entry/domain routing as separate unmerged features.
2. Use an authorized read-only material and association provider to implement
   `material_id + version_no + source_hash` revision binding. Do not scan or
   mutate users' original DB from independent CI.
3. Add entry actions to each existing mature workbench **only on an additive,
   disable-by-default integration branch**, without rewriting existing pages.
4. Map verified formal source fields to the existing PATCH57 five-dimensional
   scenario model; preserve software/hardware/mechanical typed failures.
5. Generate single-issue candidates and only after human confirmation consider
   historical ScenarioAssets grouping; P04 is a separate public consumer.
6. Test original full baseline, independent contract tests, real authorized
   materials, native macOS, and other workbenches before asking for merge.

## 4. Isolation

This draft PR does **not** import the PATCH57 runtime or the unmerged #621
module. It adds only new files, no original modules modified. No Provider
invocation, real database access, scenario generation, source mutation,
legacy regression or native macOS UAT is claimed.

PR #610 and PR #621 remain Draft, unmerged. No branch/PR is a release baseline.
