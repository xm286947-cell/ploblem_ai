# Hardware R1 Single-Package E2E Validation

This validation ZIP reuses the existing Hardware Case Web and runtime. It is a test package, not a product release. Start it with `START_HARDWARE_R1_E2E_VALIDATION.command` (macOS) or `.bat` (Windows), then use the single page shown by the launcher.

The application stores persistent validation data under the operating system's HardwareR1E2E data directory, separate from the normal product data root. Do not move or copy its database files. Dataset manifests, source checksums, and validation-root bookkeeping are internal audit details.

## Local setup

Python 3.11+ and the dependencies in `requirements.txt` must be available. Copy `config/runtime/model.local.example.yaml` to `config/runtime/model.local.yaml`, configure a permitted model/provider using environment-variable references, and set `HARDWARE_CASE_MODEL_CONFIG` to that local file. Keep credentials in the operating-system environment/key store, not in this ZIP.

The first launch opens the existing Hardware Case Web on `http://127.0.0.1:8080/p0/hardware-cases/e2e`. The launcher defaults to an isolated `LOCAL_NON_PROD` Unified Knowledge repository under the E2E data root. It reuses the existing Unified Knowledge public facade and immutable `KnowledgeReleaseService`; it is not a second Knowledge platform. After an explicit human Publish, the validation profile creates or advances an immutable NON_PROD Knowledge Release so Query Back can execute immediately without manual release stitching.

The safe defaults are `HARDWARE_R1_E2E_KNOWLEDGE_ENV=NON_PROD`, `HARDWARE_R1_E2E_KNOWLEDGE_MODE=LOCAL_NON_PROD`, and release prefix `HARDWARE_KNOWLEDGE_RELEASE_VERSION=HARDWARE-R1-E2E`. To bind a separately deployed NON_PROD Unified Knowledge service instead, set `HARDWARE_R1_E2E_KNOWLEDGE_MODE=EXTERNAL`, `HARDWARE_KNOWLEDGE_BASE_URL`, and `HARDWARE_KNOWLEDGE_RELEASE_VERSION`. An incomplete explicitly selected external environment remains fail-closed. Never point this validation package at production.

## Manual path

1. Import the Word documents from the Workbench page and run the batch.
2. Inspect Evidence Gate and Candidate status; resolve conflicts manually.
3. Use Promotion precheck and Candidate Intake, then perform Formal Review explicitly.
4. Publish explicitly to NON_PROD Unified Knowledge. In the default local validation mode, the package advances the immutable NON_PROD release snapshot after that explicit Publish.
5. Verify and Query Back, update the rebuildable Consumption Projection, search, open the correct knowledge detail, and use E2E-mode Evidence controls to inspect the source passage or open the original Word.

No step auto-reviews or auto-publishes. CI uses synthetic DOCX and fake providers only; it never includes real corpus files or makes real provider calls. Earlier Prepare and Execution packages remain internal engineering tools and are not part of this package or its user path.

Scope is limited to Hardware R1 E2E validation. This ZIP does not certify the whole Hardware Case MVP, a formal product release, or installer packages.
