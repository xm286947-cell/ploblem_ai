# Public Knowledge Service infrastructure baseline

This directory implements the candidate-independent macOS substrate for issue #385. It is isolated from Storage product data and uses only explicit PUBLIC source input.

## Runtime topology

- macOS hosts Docker Desktop and Docker Compose.
- The API container binds to `127.0.0.1:8080` and is limited to 4 GiB and four CPUs.
- Generation calls the separately hosted Ollama at `192.168.1.100`; Ollama is not installed on this Mac.
- The configured test model is pinned by the measured Ollama tag and digest. The service refuses inference if either differs.
- Parser, chunker, retriever, generation provider, and manual-provider contracts are replaceable. The included UTF-8 text parser and simple SQLite lexical adapter are bootstrap references, not final technical selections.

## Start, stop, and health

```sh
bash start.sh
bash scripts/health.sh
bash stop.sh
```

Defaults use the measured remote Ollama address, pinned model tag and digest, and bounded resource settings. Optional overrides may be placed in `public_knowledge_rag/.env`; that file is ignored by Git. `start.sh` builds and starts the Compose service, then waits for `/health`. The service stores source metadata, version content, chunks, and fixtures in a named Docker volume.

## Contract

- `GET /health`
- `GET /config`
- `POST /sources/import`
- `GET /sources`
- `GET /sources/{source_id}`
- `POST /search`
- `POST /ask`
- `GET /citations/{citation_id}`
- `POST /fixtures/capture`
- `GET /fixtures/{fixture_id}`
- `POST /fixtures/replay`

Import requires `classification=PUBLIC`. Only UTF-8 `text/plain` and `text/markdown` are accepted by the bootstrap parser; unsupported documents fail closed until an architecture-selected parser adapter is installed. Import requests carry parsed text; the service does not fetch arbitrary URLs. A supplied URL is provenance metadata and must be public HTTP(S).

Search uses the replaceable lexical reference adapter. `LIVE` calls the pinned remote model; fixture replay returns the captured response without contacting a provider. Storage receives the same answer/citation shape in either mode.

Manual evaluation has an explicit adapter slot that remains unconfigured and fails closed. No provider key is required by this service.

## Verification

```sh
.venv/bin/python -m pip install -r tests/pkr/requirements-test.txt
.venv/bin/pytest -q tests/pkr
OLLAMA_URL=http://192.168.1.100:11434 \
OLLAMA_MODEL=qwen3-vl:8b-thinking-q4_K_M \
OLLAMA_MODEL_DIGEST=901cae73216286ea8c5aba8b46d307ff7188f737285ec500c795a12f05225d28 \
OLLAMA_NUM_PREDICT=512 \
  .venv/bin/python scripts/verify_remote_ollama.py
```

The test model above is the currently observed Qwen model on the remote Ollama host. The exact model and digest are configuration facts for this infrastructure verification; parser/vector/embedding/retrieval selection for #387 remains separate.

## Data boundary

Do not place confidential/customer/internal source material or credentials in requests, fixtures, logs, evidence, or this repository. The default listener is loopback-only. Keep `.env` untracked. The application never prints request bodies and disables HTTP access logging.
