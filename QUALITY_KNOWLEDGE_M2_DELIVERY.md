# QUALITY KNOWLEDGE M2 DELIVERY

Version: REPEAT_CASE_ENGINE_V2.4_QUALITY_KNOWLEDGE_M2
Status: Released

## Scope
M2 — AI Analysis

## Delivered
- OccurrenceAnalyzer: Why Occurred / root cause / failure mechanism / contributing factors
- EscapeAnalyzer: Why Escaped / verification gap / process gap / escape mechanism
- RecurrenceAnalyzer: recurrence risk / existing control coverage / residual risk
- CapabilityGapAnalyzer: TECHNICAL / MANAGEMENT / GOVERNANCE gaps
- Evidence-bearing DTOs and strict Pydantic validation
- analysis_run versioning with model, prompt, schema, engine, input hash and error trace
- issue_ai_analysis immutable history
- issue_capability_gap structured persistence
- single-case isolated prompting
- AI failure isolation; previous success is preserved
- customer name minimized from AI context by default
- CLI: run-quality-issue-analysis

## CLI
```bash
python main.py run-quality-issue-analysis --knowledge-id <ID>
python main.py run-quality-issue-analysis --business-type PLC --only-missing
```

## Regression
- M1 + M2 targeted: 6 passed
- Full suite: 135 passed, 0 failed

## Not in M2
- M3 query aggregation and XLSX/CSV export
- cross-case AI summarization
- Repeat Case retrieval consumption of new gap fields
