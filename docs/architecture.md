# 架构

```text
cases.xlsx -> Excel Parser -> raw_excel
PDF目录    -> Matcher/PDF Parser -> raw_evidence
raw_excel + raw_evidence -> Evidence Fusion -> standard_case
standard_case -> AI Enricher(M5) -> enriched_case
```

Raw Layer、事实层和AI增强层不得相互覆盖。
