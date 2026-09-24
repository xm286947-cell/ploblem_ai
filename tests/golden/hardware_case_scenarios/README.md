# Hardware Case S1–S4 synthetic golden set

All names, measurements, Word files, and trees in this folder are invented for mock tests. No company samples or identifiers are included.

- `A9001`–`A9012`: 12 synthetic `.docx` cases, including missing root cause, missing action, multiple circuit/material candidates, an image attachment, alternative headings, and no tree match.
- `circuit_feature.xlsx` and `material.xlsx`: independent mock trees.
- `expected.json`: hand-specified expected fields, candidate node IDs, and fields requiring source evidence.
- `generate.py`: reproducible fixture generator. Run `python tests/golden/hardware_case_scenarios/generate.py` to rebuild the files.

Run `python -m pytest -q tests/test_hardware_case_scenario_poc.py tests/test_hardware_case_ai_adapter.py` from the repository root. The harness reads the Word and Excel files and writes only a temporary SQLite database. It uses the existing parser, adapter, repository, and backend. The six `SK-HC-01`–`06` mock interfaces live in `services/hardware_case_scenario_poc.py`; the real provider path remains `HardwareCaseRuntimeStructurer` in the existing Unified Runtime integration. Query results expose **SUGGESTED** mappings to the PoC maintainer only. No candidate is confirmed or published.

For later company-only validation, provide an approved in-environment model configuration, read-only locations for 20–30 Word documents and the two Excel trees, and an authorized reviewer. Run the existing `tools/hardware_case_real_validation.py` flow inside that environment. Only aggregate metrics and sanitized error codes may leave it.
