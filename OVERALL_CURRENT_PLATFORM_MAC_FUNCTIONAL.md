# Mature Platform Foundation Functional Candidate

This candidate intentionally restores the existing **mature Quality Issue host** as the product runtime root before any further domain integration.

Runtime root:

```text
main.py knowledge-web
→ quality_knowledge.web.app.create_app
→ /issues
```

Default entry:

```text
/issues
```

Product rules for this phase:

- P0 is not a product entry and `/p0/overall` is not a release target.
- No new FastAPI host or replacement Overall shell is introduced.
- Existing mature Quality routes and data semantics stay authoritative.
- Existing QualityScenario V1 capability is mounted additively into the same mature host.
- Major, Hardware, and Storage composition changes are deliberately deferred to later phases.
- P0 implementation files are retained temporarily until their consumers are migrated and dependency count reaches zero.

Launch:

```bash
./START_OVERALL_CURRENT_PLATFORM_MAC.command
```

or on Windows:

```bat
START_OVERALL_CURRENT_PLATFORM_WINDOWS.bat
```

Both wrappers delegate to the existing mature launcher and default to:

```text
http://127.0.0.1:18080/issues
```

Data binding:

```text
LEGACY_QUALITY_ISSUE_DB_PATH=optional explicit mature DB binding
QUALITY_SCENARIO_V1_DB_PATH=optional independent QSV1 DB binding
default mature DB=knowledge/quality_issue_v1.db
P0/P1 initialization=NOT_USED_BY_PRODUCT_LAUNCHER
```

Validation classification:

```text
PACKAGE_SCOPE=MATURE_PLATFORM_FOUNDATION
MATURE_PRODUCT_IS_RUNTIME_ROOT=YES
P0_PRODUCT_ENTRY=NO
NEW_HOST_CREATED=NO
MAJOR_INTEGRATION=DEFERRED
HARDWARE_INTEGRATION=DEFERRED
STORAGE_INTEGRATION=DEFERRED
NEXT=QUALITY_SCENARIO_MATURE_INTEGRATION_CLOSURE
```
