# KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_ENGINE V1.0 M3
Status: Released
Design: KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_DESIGN_V1.0 Frozen
Baseline: ENGINE_V1.0_M2

## Delivered
- Current Issue Version scoped AI analysis
- A1/A2/A3/A4: Occurrence, Escape, Recurrence, Capability Gap
- Versioned Analysis Run: RUNNING / COMPLETED / FAILED
- Evidence/confidence/model/prompt/schema/engine/input hash trace
- Capability Gap taxonomy fields for Technical / Management / Governance
- Single issue analysis, batch analysis, only-missing, retry by rerun
- Analysis history and current-version latest result
- Web AI Analysis page + Issue Detail analysis + REST endpoints
- CLI `knowledge-analyze`
- Failure isolation: failed run preserves facts and previous completed runs

## Validation
- M3 baseline tests: 2 passed
- Full regression: 146 passed, 0 failed
