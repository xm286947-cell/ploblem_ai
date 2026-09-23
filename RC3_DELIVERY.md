# KNOWLEDGE QUALITY ISSUE ANALYSIS ENGINE V1.0 M5 RC3

## Scope
AI Response Robustness / Parsing / Diagnostics fix based on RC2.

## Fixed

1. AI normal response no longer fails only because EvidenceValue fields are returned as strings.
2. Added stage response normalization for Occurrence / Escape / Recurrence / Capability Gap.
3. Added explicit JSON templates to all four quality issue AI prompts.
4. Added `analysis_run_debug` persistence for:
   - raw_response
   - parsed_json
   - normalized_json
   - validation_error
5. Failed parsing preserves the raw model response and validation error for diagnosis.
6. Successful normalized AI results remain persisted after repository restart.
7. Prototype / Reference analysis service remains compatible with the new analyzer return contract.

## Runtime pipeline

```text
LLM Response
  ↓
Raw Response persistence
  ↓
JSON extraction / repair
  ↓
StageResponseNormalizer
  ↓
DTO / Pydantic validation
  ↓
Derived persistence
```

## Compatibility examples

The following LLM response is now accepted:

```json
{
  "root_cause_summary": "版本变更引入逻辑错误",
  "failure_mechanism": "异常输入触发错误分支",
  "contributing_factors": ["变更影响分析不足"],
  "occurrence_category": "CHANGE",
  "confidence": 0.88,
  "evidence": []
}
```

It is normalized internally to the strict EvidenceValue DTO format before persistence.

## Test result

```text
RC3 focused tests: PASS
Full regression: 157 passed / 0 failed
```
