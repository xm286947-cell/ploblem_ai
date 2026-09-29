# STEP1 Legacy Quality Scenario / Portrait Restore Source

TASK=STEP1-LEGACY-QUALITY-SCENARIO-AND-PORTRAIT-RESTORE-001
ISSUE=#299

MATURE_SOURCE_BRANCH=release/quality-scenario-mvp-rc1-test-package
MATURE_SOURCE_COMMIT=ce2eca157c4403b97036dd66e5218cd74fe53700
TARGET_BASE=9ac6ae0602d7ac2812e3f063b2231424094e2ee9

Restored mature user routes:
- /quality-scenarios
- /quality-scenario-assets
- /quality-scenario-assets/portrait

Composition boundary:
- start_quality_capability_p1.bat -> main.py knowledge-web -> create_app: RESTORED
- Overall R2 create_legacy_quality_issue_router: UNCHANGED / NOT MOUNTED

The RC1 domain modules and templates are restored as their original blobs.
The only adaptation is the standalone composition attachment needed by the
current Web architecture. No UED redesign, Storage change, Hardware change,
or /p0/quality-scenario-insights substitution is included.
