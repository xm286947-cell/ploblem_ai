# Overall VNext Browser E2E

TASK=OVERALL-VNEXT-BROWSER-E2E-001
STATUS=PASS
DATA_MODE=SYNTHETIC_ISOLATED
REAL_PROVIDER_CALL=NO
FORMAL_S11=NOT_RUN

## Scope

The test starts `scripts/overall_vnext_demo.py` on the existing single FastAPI
host and drives the rendered product with headless Chromium. It uses an
isolated data directory and the repository's synthetic P04, Legacy, Hardware,
Storage, Knowledge, and Overall task fixtures.

## Result

Nine browser checks passed:

1. Overall Shell renders and exposes the four product workspaces.
2. All five VNext product area pages render.
3. Major, Quality Scenario, Hardware, and Storage workspace bindings resolve.
4. The synthetic Legacy issue `ITR-VNEXT-DEMO-001` is visible.
5. P04 preserves INDUSTRY state through `QS-FIX-002 → PROBLEM-003 → Return`.
6. Common Evidence renders with `common-evidence/v1.0`.
7. Storage returns to Overall on the same host.
8. The Overall Shell renders at a 390 × 844 mobile viewport.
9. The mobile page has no horizontal overflow.

Browser console errors: **0**.
Failed browser requests: **0**.

The Linux executor does not contain CJK glyph fonts. Chromium screenshots
therefore show fallback boxes for Chinese glyphs, while DOM text, navigation,
responsive layout, and assertions remain valid. Chinese typography still needs
visual confirmation on a normal mobile or desktop runtime.

## Reproduce

```bash
python -m pip install -r requirements-browser-test.txt
python -m playwright install chromium
python scripts/overall_vnext_browser_e2e.py \
  --evidence-dir /tmp/overall-vnext-browser-e2e-evidence \
  --data-dir /tmp/overall-vnext-browser-e2e-data
```

The evidence directory contains `result.json`, `dut.log`,
`overall-desktop.png`, and `overall-mobile.png`.

This is development-side browser validation. It does not claim the formal S11
regression or Product Test Gate.
