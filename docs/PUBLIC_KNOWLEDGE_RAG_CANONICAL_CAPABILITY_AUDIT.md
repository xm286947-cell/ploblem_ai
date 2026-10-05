# Public Knowledge RAG Canonical Capability Audit

Date: 2026-10-05
Canonical branch: `integration/public-knowledge-rag`
Canonical start: `aeb729e12e52e56aab49354d018301c7acbaf116`

## Decision and scope

This branch selectively combines the accepted provider/admin work from PR #425 with the accepted multi-format ingestion work from PR #437 on the component-reselect baseline. It does not merge the BGE-M3 experiment and does not change Storage or Knowledge Production.

| Input | Treatment | Result |
|---|---|---|
| `feature/public-knowledge-rag-component-reselect-001` at `aeb729e12e52e56aab49354d018301c7acbaf116` | Canonical start | Preserved as the first parent in branch history |
| #425 `02e1c5a395234dd491898a4661c90ebff0243db9` | Selective transplant | Provider configuration page; rewritten as `09c6e9a` |
| #425 `73456783905d3f2fbc1de3f95b6b66c4fbbe51d5` | Selective transplant | OpenAI-compatible generation; rewritten as `fa27e4c` |
| #425 `9e4b03dcb845a846140f17b14a04da151eb3631f` | Selective transplant | Current chat output token parameter; rewritten as `e0330bf` |
| #425 `963f6ecf86afb7854f362647b003e79dece29797` | Selective transplant | Supervised restart; rewritten as `bb901fd` |
| #425 `00e782cd3eae5331f953a637d221bc21f263e1e8` | Selective transplant | Host port contract; rewritten as `637bc90` |
| #437 `8ad9afe7748be18d2c0336bb525fcb6b06b7d16b` | Selective transplant | Source-preserving multi-format ingestion; rewritten as `dd74c10` |
| BGE-M3 experiment `1dece9ea06fb855b3ecd38c028fa03dbb058bef5` | Explicitly excluded | Not an ancestor of this branch; its BGE-only runner is absent |

`VALID_ACCEPTED_CAPABILITY_LOST=0`
`EXPERIMENTAL_DELTA_LANDED=0`

## Capability coverage

### Provider and service administration

- The service settings page supports Ollama and `openai_compatible` generation providers.
- OpenAI-compatible protocol selection is explicit: `chat_completions` or `responses`.
- The provider sends a model generation request for Test Connection and uses the selected provider for `/ask`; it does not silently fall back to Ollama.
- API credentials are write-only in the UI and redacted from configuration reads. The local secret store is separate from `config.local.json` and uses mode `0600`. Environment credentials remain supported.
- Configuration validation, provider test, save, effective-state display, and controlled supervised restart are available. The UI waits for the service instance to return and confirms the saved configuration is active.
- The restart endpoint only requests a supervised process restart; it does not expose arbitrary shell execution or a Docker socket.
- Service settings show credential status without revealing the credential.

### Multi-format source preservation

The same API supports PDF, DOCX, HTML, Markdown, and TXT ingestion. Each source revision retains the original bytes and SHA-256 snapshot. Search results and citations retain format-specific locators back to the original source. A PDF normalized representation is not treated as the formal source evidence.

An isolated Docker deployment was built and exercised for all five formats. The tests verified byte-identical source snapshots, source SHA-256, successful search/citation, and format-specific page/section/table/anchor/line locator data.

The PUBLIC-only source classification check remains enforced at the service boundary. Search/QA errors fail closed; the service does not return a fabricated answer when generation is unavailable.

### Retrieval status retained without retuning

The component-reselect history and its HybridChunker/RRF implementation and tests remain in the canonical branch. This task did not change retrieval tuning. The active request path in `public_knowledge_rag/app.py` still wires `WindowChunker` and `SQLiteLexicalRetriever` (reported by the API as `sqlite-lexical-reference`). This audit does not claim that the live API has switched to HybridChunker, Qdrant, or dense-plus-lexical RRF.

`RAG_TUNING_REOPENED=NO`

## Port contract

- Operator, user, and Storage URL: `http://127.0.0.1:9000`
- Container listener and healthcheck: port `8080`
- Canonical Compose mapping: `127.0.0.1:9000:8080`
- Host-facing health, smoke-script, startup, and settings documentation defaults use port `9000`.

Static contract tests verify the mapping and the separation between the host port and internal container port. Docker verification used an isolated test override mapping host port `19000` to container port `8080` to avoid taking over the user's port. The canonical Compose file itself was built and its default `9000:8080` mapping was checked.

## Verification evidence

- `python -m compileall -q public_knowledge_rag scripts/e2e_smoke.py`: PASS.
- `.venv/bin/python -m pytest -q tests/pkr tests/test_public_knowledge_rag_tech_selection.py`: 47 passed.
- Headless Chrome browser smoke at the isolated service settings page: PASS; provider state loaded, the OpenAI-compatible provider was selected, Responses Test Connection succeeded, and no browser JavaScript errors occurred.
- Isolated Docker Compose build and service health: PASS.
- Ollama and OpenAI-compatible Test Connection/`/ask` were exercised against a local deterministic HTTP provider fixture. Both explicit OpenAI-compatible protocols are covered by automated provider tests; the Responses protocol was also exercised through the actual settings page and running service.
- Secret check: test credential absent from GET configuration, `config.local.json`, and container logs; present only in `secrets.local.json` with mode `0600`.
- `git diff --check`: PASS.
- Real OpenAI account/provider request: `NOT_RUN`. No `OPENAI_COMPATIBLE_API_KEY` was configured in this environment. The local deterministic provider fixture does not count as a real OpenAI verification.

## Boundaries and status

`STORAGE_CHANGED=NO`
`KNOWLEDGE_PRODUCTION_CHANGED=NO`
`RAG_TUNING_REOPENED=NO`
`WAVE3_STARTED=NO`
`REAL_OPENAI_PROVIDER=NOT_RUN`
`NEXT_REAL_PROVIDER_VALIDATION=#426`

This canonical implementation is ready for canonical RAG product validation. Do not treat it as a Product Gate, release, or real-provider pass. Keep the canonical PR unmerged until the real OpenAI-compatible provider validation is explicitly completed.
