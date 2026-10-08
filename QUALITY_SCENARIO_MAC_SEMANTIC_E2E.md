# Quality Scenario macOS Semantic E2E

This gate answers a different question from the earlier G1-G5 functional Golden:

> Are the new Quality Scenarios actually the scenarios the product should keep?

The prior controlled-provider Golden proves mechanics. Its provider intentionally returns one fixed power-loss payload, so it cannot prove cross-case semantic quality. This semantic gate uses the same controlled source data but a real provider through Unified Runtime.

## Run on macOS

Preferred local configuration:

```text
config/runtime/model.local.yaml
```

This file is local-only and must not be committed. Alternatively export the endpoint/key expected by `config/runtime/model.yaml`.

Then run:

```bash
/bin/zsh ./start_quality_scenario_mac_semantic_e2e.command
```

The run:
- constructs only source-side G1-G5 data;
- opens/uses the mature `/software-assessment` UI through Playwright;
- invokes the real provider;
- never pre-seeds Candidate/Review/Publish;
- exports exact generated scenario JSON, traceability and browser screenshots;
- creates `validation/mac_semantic_e2e/SEMANTIC_ACCEPTANCE_REPORT.md`.

## Product review criteria

1. Source fidelity.
2. Reusable scenario abstraction rather than incident copying.
3. Trigger/precondition quality.
4. Failure mode / quality risk meaning.
5. Expected quality result.
6. Testability.
7. Evidence traceability and no overclaim.
8. Would the product owner keep this scenario in the library?

Automatic keyword checks are only a smoke layer; human product review is authoritative.
