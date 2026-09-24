# QUALITY_SCENARIO_MVP_RC1 Known Limitations

1. Internal real-problem Golden is not stored in GitHub. Repository CI uses a sanitized synthetic case. Final release requires a company-environment run with real internal problem material.
2. PC first. RC1 is optimized for 1440×900 and remains usable around 1280px; mobile-specific product design is out of scope.
3. Three main pages only. P01 Workbench, P02 Library, P03 Detail. Evidence stays in the page context / drawer.
4. No portrait or quality-standard generation. Customer/industry/product portraits, quality metrics, test standards and quality gates are deferred.
5. No approval workflow. Professional-quality and R&D confirmations are facts on the V1 object; there are no WAIT_APPROVAL-like states.
6. Controlled content references may not contain inline source text. reverse-quality:// means a controlled pointer exists; the product does not claim the original text has been read when source_text is empty.
7. Legacy Scenario remains compatibility-only. RC1 formal assets use QualityScenario V1; the legacy Scenario path is not silently migrated.
8. No second Evidence database. Traceability uses the V1 source/evidence relations and existing audit/version tables.
