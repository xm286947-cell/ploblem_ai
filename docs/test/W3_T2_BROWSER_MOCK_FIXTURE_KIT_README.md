# W3 T2 Browser Mock Fixture Kit (INTERNAL TEST ONLY)

Product immutable source SHA: `c7935e00f1f0abe828b0090524bb14ba923f3635`

This **test-only** ZIP contains two legitimate synthetic Word files, the exact parsed Evidence ID mapping, frozen Stage A/B schema-valid JSON responses, and a local Stage dispatch wrapper around **existing** `tools/openai_mock/server.py`. It does not modify product code, prompts, Runtime or Knowledge, and includes no real API key.

## Start (Python 3.11)

1. Extract the delivered W3 internal Candidate ZIP separately. The extracted root contains `services/`, `tools/`, `config/`, and `START_HARDWARE_W3_MOCK_TEST.bat` / `.command`.
2. Extract **this** fixture kit separately. Keep the `w3_tse_browser_mock_fixtures.py` file with the generated synthetic files. From an activated Python 3.11 environment with the candidate's requirements installed, execute:

   Windows (PowerShell):
   ```powershell
   py -3.11 .\w3_tse_browser_mock_fixtures.py --product-root "C:\path\to\HARDWARE_W3_INTERNAL_TEST_CANDIDATE_c7935e0" --out-dir ".\fixtures" --serve
   ```
   macOS:
   ```bash
   python3.11 ./w3_tse_browser_mock_fixtures.py --product-root "/path/to/HARDWARE_W3_INTERNAL_TEST_CANDIDATE_c7935e0" --out-dir "./fixtures" --serve
   ```

3. In a separate terminal start the W3 Candidate using `START_HARDWARE_W3_MOCK_TEST.bat` (Windows) or `.command` (macOS). These launch only on `127.0.0.1:18782`; Mock listens only on `127.0.0.1:18783`.
4. Verify separately: `http://127.0.0.1:18783/__mock__/health` and `http://127.0.0.1:18782/ready`. Both are readiness checks only: **they do not prove that G3 passes**.
5. In the isolated Workbench page `http://127.0.0.1:18782/p0/hardware-cases/knowledge-production` upload **only** the two files `A9903-....docx`, `A9904-....docx` from this kit; opt into PARALLEL, concurrency=2, and perform the explicit Test Center-approved mock-only Run. Preserve screenshots, Workbench batch ID, `/__mock__/counters` and `/__mock__/requests`, Runtime attempt trace and Evidence validation.
6. Do **not** click Review/Publish. Do **not** use previously tested A9901/A9902; those have already completed G4. Never attach a real Provider to this test config.

## Stage dispatch mechanism
Frozen W3 OpenAI Compatible provider sends a standard `POST /v1/chat/completions` body with a JSON user message. The test-only Mock extracts `input_contract` from that user message and chooses the preconfigured `stage-a` or `stage-b` scenario. Unsupported contracts get HTTP 400. The existing provider interface is unchanged, no injected `X-Mock-Scenario-Key` required in Product.

The sample Word bodies are generated/validated using `services.hardware_case_word.parse_docx`; block IDs (B0001–B0005) are read from **actual parsed DOCX**, and the Stage A/B fixture references those IDs. Both fixtures are schema checked using `HARDWARE_R1_STAGE_A_SCHEMA` and `HARDWARE_R1_STAGE_B_SCHEMA`. If your browser dataset differs, **do not reuse these fixed fixtures**; generate source-specific fixtures and get a dedicated test bound.

## Status

- Python 3.11 independent T1: 20/20 by TSE report.
- T2 G4: PASS by TSE report, do not replay.
- Stage A/B real HTTP mock Python 3.11 three OS: 4/4 on each, CI #37888811619.
- This fixture kit's own CI selftest: see GitHub Actions job and artifact SHA.
- T2 browser G3/G5–G8: NOT YET PASSED. This kit enables G3 but does not certify it.
- Windows/macOS internal Candidate Fresh Extract GET-only: PASS on both CI runners #37885979327.
- Real Provider / formal Publish: NOT RUN. Formal acceptance: NOT READY.
