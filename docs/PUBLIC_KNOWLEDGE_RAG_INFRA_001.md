# Public Knowledge Service infrastructure baseline

This directory implements the candidate-independent macOS substrate for issue #385. It is isolated from Storage product data and uses only explicit PUBLIC source input.

## Runtime topology

- macOS hosts Docker Desktop and Docker Compose.
- The operator-facing API is `http://127.0.0.1:9000`; Compose maps `127.0.0.1:9000:8080`.
  The service listens on container-internal port `8080` and is limited to 4 GiB and four CPUs.
- Generation calls the separately hosted Ollama at `192.168.1.100`; Ollama is not installed on this Mac.
- The configured test model is pinned by the measured Ollama tag and digest. The service refuses inference if either differs.
- Parser, chunker, retriever, generation provider, and manual-provider contracts are replaceable. The included UTF-8 text parser and simple SQLite lexical adapter are bootstrap references, not final technical selections.

## Start, stop, and health

```sh
bash start.sh
bash scripts/health.sh
bash stop.sh
```

The operator URL is `http://127.0.0.1:9000`; health is `http://127.0.0.1:9000/health` and settings are `http://127.0.0.1:9000/settings`. Inside the container, Uvicorn and the Compose healthcheck use port `8080`. Defaults use the measured remote Ollama address, pinned model tag and digest, and bounded resource settings. Optional overrides may be placed in `public_knowledge_rag/.env`; that file is ignored by Git. `start.sh` builds and starts the Compose service, then waits for the operator-facing `/health` URL. The service stores source metadata, version content, chunks, and fixtures in a named Docker volume.

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

Import requires `classification=PUBLIC`. File import supports PDF, DOCX, HTML, Markdown, and TXT through deterministic local format adapters. Original bytes are stored as immutable source snapshots with SHA/revision metadata and format-native locators; parsing does not invoke the generation provider. The JSON text-import endpoint remains available for `text/plain` and `text/markdown`. The service does not fetch arbitrary URLs; a supplied URL is provenance metadata and must be public HTTP(S).

Search uses the service's configured lexical retrieval adapter. `LIVE` calls the configured generation provider; fixture replay returns the captured response without contacting a provider. Storage receives the same answer/citation shape in either mode.

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

Do not place confidential/customer/internal source material or credentials in requests, fixtures, logs, evidence, or this repository. The host-facing default listener is loopback-only at port 9000; container-internal service and healthcheck traffic use port 8080. Keep `.env` untracked. The application never prints request bodies and disables HTTP access logging.
