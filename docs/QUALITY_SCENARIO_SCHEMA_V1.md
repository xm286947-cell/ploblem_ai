# Quality Scenario V1 Contract

**Task:** QS-MVP-02 — Quality Scenario V1 Schema Freeze  
**Schema version:** `quality-scenario-v1`  
**Status:** implementation candidate for Product Gate

## 1. Contract boundary

Quality Scenario V1 is the single business contract used by Candidate, Review,
Publish, Repository, UI and Evidence in the MVP.

It does **not** replace:
- ReverseQualityResult V0.1;
- Reverse Quality Store;
- Unified Agent Runtime;
- lifecycle / business-activity taxonomy;
- legacy `ScenarioGenerationService.generate()`.

The reverse production path is:

```
Problem / ITR
  -> ReverseQualityResult V0.1
  -> deterministic adapter (no second AI pass)
  -> ScenarioCandidateV1
  -> QualityScenarioV1(CANDIDATE)
  -> human review
  -> CONFIRMED
  -> publish
  -> PUBLISHED
```

## 2. QualityScenarioV1

### Identity / governance
- `scenario_id`: stable V1 scenario ID.
- `schema_version`: always `quality-scenario-v1`.
- `scenario_version`: integer revision, starts at 1.
- `status`: CANDIDATE / CONFIRMED / PUBLISHED / REJECTED.
- `review`: candidate-level human review result; it is not an approval workflow.
- `confirmation`: lightweight professional confirmation facts:
  - `quality_confirmed_by` / `quality_confirmed_at`;
  - `technical_confirmed_by` / `technical_confirmed_at`;
  - optional `confirmation_note`.
- `version`: created_by / created_at / updated_at / published_at /
  parent_scenario_version / change_summary.

### Ownership / scope
- `product_code` / `product_name`
- `lifecycle_stage_code` / `lifecycle_stage_name`
- `business_activity_code` / `business_activity_name`
- `business_goal`

### Scenario definition
- `scenario_name`
- `scenario_description`
- `quality_concern_code` / `quality_concern_name`
- `trigger_source`: `HIGH_PERCEPTION` / `RND_VALUE`
- `trigger_reason`
- `trigger_condition`
- `expected_result`
- `applicability_scope`

### Traceability
- `source_problem_refs[]`
- `evidence_refs[]`

### Gate context
- `blockers[]`
- `missing_information[]`

Candidate may contain blockers or pending missing information. CONFIRMED and
PUBLISHED may not.

`trigger_source` records why the problem entered deep scenario analysis. It is
business-source metadata only; it does not create a second workflow or state
machine. RC1 has exactly two values: `HIGH_PERCEPTION` and `RND_VALUE`.

## 3. ScenarioCandidateV1

ScenarioCandidateV1 shares the V1 business fields and adds production metadata:
- `candidate_id`
- `source_result_version`
- `source_analysis_id`
- `source_run_id`
- `source_run_seq`
- `producer`
- `field_evidence`

Candidate status is fixed to `CANDIDATE`.

The current ReverseQualityResult adapter is reused. The V1 adapter does not
read the original issue again and does not call AI.

## 4. Source problem reference

`ScenarioSourceReference`:
- `source_ref`: local stable reference used by evidence.
- `source_type`: ITR / other controlled source type.
- `source_id`
- `canonical_itr`
- `product_code`
- `product_version`
- `relation_type`: PRIMARY / SUPPORTING / RELATED.

A formal scenario must have at least one source problem/control source.

## 5. Evidence reference

`ScenarioEvidenceReference`:
- `evidence_id`
- `source_ref`
- `evidence_type`
- `source_text` or controlled `content_ref`
- `supports[]`: V1 fields supported by the evidence
- `source_type`: FACT / INFERRED / HUMAN_CONFIRMED
- `confidence` when applicable

Rules:
- at least one of source_text/content_ref is required;
- evidence.source_ref must resolve to source_problem_refs;
- AI-derived content is never silently promoted to FACT;
- Runtime execution logs are not product Evidence.

## 6. State machine

Allowed:
- CANDIDATE -> CONFIRMED
- CANDIDATE -> REJECTED
- CONFIRMED -> PUBLISHED

Forbidden:
- CANDIDATE -> PUBLISHED
- AI -> CONFIRMED
- AI -> PUBLISHED
- PUBLISHED -> any MVP state
- REJECTED -> any MVP state

CONFIRMED/PUBLISHED gates:
- no blockers;
- no PENDING missing_information;
- lifecycle and business activity are mapped;
- expected_result is present;
- quality concern has code or name;
- `trigger_source` is one of HIGH_PERCEPTION / RND_VALUE;
- `trigger_reason` is present;
- source problem exists;
- evidence exists;
- human review is CONFIRMED;
- professional-quality confirmation has actor + time;
- R&D technical confirmation has actor + time.

The two confirmation records are business facts, not approval states. They do
not introduce WAIT_APPROVAL / WAIT_QUALITY_APPROVAL / WAIT_RND_APPROVAL or any
other state outside the four-state MVP machine.

Publishing failure must leave the object in CONFIRMED; the publish action itself
belongs to QS-MVP-04, not QS-MVP-02.

## 7. SQLite / Repository V1

The frozen V0.2 requirement adds the following persisted columns to
`quality_scenario_v1`:

- `trigger_source`
- `trigger_reason`
- `quality_confirmed_by`
- `quality_confirmed_at`
- `technical_confirmed_by`
- `technical_confirmed_at`
- `confirmation_note`

Initialization includes an idempotent additive migration for a database created
by the earlier QS-MVP-02 candidate schema. Legacy `quality_scenario` remains
untouched.



Recommended database: `quality_scenario_v1.db`.

V1 uses an independent table namespace:
- `quality_scenario_v1`
- `quality_scenario_v1_source`
- `quality_scenario_v1_evidence`
- `quality_scenario_v1_review`
- `quality_scenario_v1_version`
- `quality_scenario_v1_meta`

The repository supports:
- create from Candidate;
- save/update under the state contract;
- get;
- list/filter;
- delete (repository-level CRUD support).

Query dimensions are indexed for:
- product;
- lifecycle stage;
- business activity;
- quality concern;
- status.

Initialization is idempotent. If V1 tables are created in a database that also
contains legacy `quality_scenario`, the legacy table is not altered.

## 8. ReverseQualityResult -> Candidate V1 mapping

The accepted `adapt_reverse_quality_result()` remains the first deterministic
mapping. V1 promotes its output as follows:

| Reverse/adapter output | V1 field |
| --- | --- |
| product_code | product_code |
| lifecycle_code | lifecycle_stage_code |
| activity_code | business_activity_code |
| activity objective | business_goal |
| name | scenario_name |
| customer_perception/failure/business impact | scenario_description |
| primary_quality_concern_code | quality_concern_code |
| concern_points / quality_attribute | quality_concern_name |
| preconditions + trigger_conditions | trigger_condition |
| experience_requirement | expected_result |
| boundary/environment/condition/objects/scale | applicability_scope |
| canonical_itr | source_problem_refs |
| upstream trigger context | trigger_source / trigger_reason |
| field_evidence.evidence_ids | evidence_refs |
| adapter blockers | blockers |
| Reverse missing_information | missing_information |

Lifecycle/activity mapping blockers are preserved and never silently removed.
ReverseQualityResult does not own the two-track trigger decision, so the V1
adapter accepts trigger context explicitly. If that context is absent it emits
`TRIGGER_SOURCE_REQUIRED` / `TRIGGER_REASON_REQUIRED` blockers rather than
guessing from AI output.

## 9. Legacy compatibility

Legacy `ScenarioRepository` and `ScenarioGenerationService.generate()` remain
unchanged in QS-MVP-02.

`legacy_scenario_to_v1_view()` provides a read-only display mapping:
- DRAFT / IN_REVIEW -> CANDIDATE
- PUBLISHED -> PUBLISHED
- RETIRED -> REJECTED (compatibility view only)

Legacy rows are **not** automatically migrated into V1 because old Evidence,
trigger-source and confirmation semantics are not equivalent. The read-only
compatibility view exposes those gaps explicitly. An explicit migration can be
designed later.

## 10. Example

```json
{
  "scenario_id": "QSV1-001",
  "schema_version": "quality-scenario-v1",
  "scenario_version": 1,
  "status": "CANDIDATE",
  "product_code": "PLC",
  "product_name": "",
  "lifecycle_stage_code": "RUNTIME_EXECUTION",
  "lifecycle_stage_name": "运行执行",
  "business_activity_code": "POWER_LOSS_RETENTION_RECOVERY",
  "business_activity_name": "掉电数据保持与上电恢复",
  "business_goal": "保证掉电后关键数据正确恢复",
  "scenario_name": "运行中异常掉电后的关键数据恢复",
  "scenario_description": "运行中异常掉电，重新上电后关键计数应保持一致",
  "quality_concern_code": "DATA_INTEGRITY",
  "quality_concern_name": "数据完整性",
  "trigger_source": "HIGH_PERCEPTION",
  "trigger_reason": "客户生产中断，属于高感知质量问题",
  "trigger_condition": "PLC运行中发生异常掉电",
  "expected_result": "重新上电后关键计数和状态正确恢复",
  "applicability_scope": "PLC运行执行阶段",
  "source_problem_refs": [
    {
      "source_ref": "ITR:ITR-001",
      "source_type": "ITR",
      "source_id": "ITR-001",
      "canonical_itr": "ITR-001",
      "product_code": "PLC",
      "product_version": "",
      "relation_type": "PRIMARY"
    }
  ],
  "evidence_refs": [
    {
      "evidence_id": "cs.description",
      "source_ref": "ITR:ITR-001",
      "evidence_type": "REVERSE_QUALITY_FIELD_EVIDENCE",
      "source_text": "",
      "content_ref": "reverse-quality://RQA-1/RQRUN-1/cs.description",
      "supports": ["scenario_description", "expected_result"],
      "source_type": "FACT",
      "confidence": 0.9
    }
  ],
  "blockers": [],
  "missing_information": [],
  "review": {
    "review_status": "PENDING",
    "reviewer": "",
    "reviewed_at": "",
    "comment": ""
  },
  "confirmation": {
    "quality_confirmed_by": "",
    "quality_confirmed_at": "",
    "technical_confirmed_by": "",
    "technical_confirmed_at": "",
    "confirmation_note": ""
  },
  "version": {
    "created_by": "reverse-quality-scenario-adapter-v0.1",
    "created_at": "2026-09-23T00:00:00Z",
    "updated_at": "2026-09-23T00:00:00Z",
    "published_at": "",
    "parent_scenario_version": null,
    "change_summary": ""
  }
}
```
