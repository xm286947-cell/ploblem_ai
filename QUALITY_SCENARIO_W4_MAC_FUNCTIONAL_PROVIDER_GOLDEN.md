# W4 macOS Functional Golden with Controlled Provider

This path is for functional closure when a real Reverse Quality provider is not
available on macOS.

It reuses the existing Unified Runtime and the repository's OpenAI-compatible
mock server. It does not bypass Reverse Quality and it does not write Candidate
or Published rows directly.

Flow:

Software Assessment Source Fact
→ frozen ScenarioSourceBundle V1
→ Unified Runtime
→ controlled OpenAI-compatible provider response
→ mature Reverse Quality validation
→ Candidate V1
→ human Review
→ dual-role Confirm
→ Publish
→ P02/P03/Evidence/History
→ product/industry/customer portraits

Classification:

- FUNCTIONAL_PROVIDER=CONTROLLED_OPENAI_MOCK
- REAL_PROVIDER_GOLDEN=NO
- DIRECT_QSV1_CANDIDATE_WRITE=NO
- DIRECT_QSV1_PUBLISH_WRITE=NO

macOS entry:

```bash
./start_w4_mac_functional_provider_golden.command --port 18080
```

The provider mock listens on localhost port 18090 only for this validation
process and is terminated when the launcher exits.
