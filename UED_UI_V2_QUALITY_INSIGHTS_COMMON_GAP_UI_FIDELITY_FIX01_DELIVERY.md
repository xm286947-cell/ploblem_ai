# UED_UI_V2_QUALITY_INSIGHTS_COMMON_GAP_UI_FIDELITY_FIX01 DELIVERY

Baseline: QUALITY_INSIGHTS_COMMON_GAP_PATCH01 + FIX01
UED Input: UED_ADDENDUM_QUALITY_INSIGHTS_COMMON_GAP_V1.0 + R4_V2_COMMON_GAP_HIFI

## Fidelity Fix
- Common Gap section follows R4 V2 HIFI hierarchy and spacing.
- Header: title/subtitle + product filter + capability-dimension filter + export.
- KPI card: Common Gaps / Related Issues / Cross-product Gaps / TOP Dimension.
- Related Issues KPI uses de-duplicated issue IDs when aggregation data provides them.
- TOP Dimension includes share percentage.
- Governance Priority uses exact horizontal ranking pattern: rank / name+dimension / horizontal bar+impact scope / drill-down.
- Platform values use compact dot-separated text in ranking rows.
- Cross-product Capability Gap secondary table is restored as required by HIFI.
- Cross-product means coverage >= 2 products/business types.
- Internal aggregate field names are not exposed in primary UI.
- Exact Gap -> Issues drill-down is not faked when backend filtering is unavailable.
- CSS is strongly scoped under cg2-* to avoid old Statistics styles overriding it.
- app.css cache key changed to `ued-r4v2-cg-fix01` so Chrome/Firefox fetch the new stylesheet.

## Compatibility
- Standards-based Grid/Flex/table/select/link implementation.
- Chrome / Firefox compatible; no browser-private API dependency.

## Test
- Common Gap HIFI / legacy Statistics compatibility focused regression: 14 passed / 0 failed.
