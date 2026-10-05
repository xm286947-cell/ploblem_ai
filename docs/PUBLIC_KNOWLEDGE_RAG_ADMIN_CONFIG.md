# Public Knowledge RAG local configuration page

Open `http://127.0.0.1:8080/settings` after starting the Public Knowledge service.
The Docker Compose port remains bound to loopback by default.

## Provider configuration

The page supports Ollama and a real OpenAI-compatible generation provider:

- `GENERATION_PROVIDER`
- `OLLAMA_URL`
- `OLLAMA_MODEL`
- `OLLAMA_MODEL_DIGEST`
- `OLLAMA_TIMEOUT_SECONDS`
- `OLLAMA_NUM_PREDICT`
- `OLLAMA_THINKING_MODE`
- `OPENAI_COMPATIBLE_BASE_URL`
- `OPENAI_COMPATIBLE_PROTOCOL` (`chat_completions` or `responses`)
- `OPENAI_COMPATIBLE_MODEL`
- `GENERATION_TEMPERATURE`
- `OPENAI_COMPATIBLE_API_KEY` (optional environment secret)

The OpenAI-compatible protocol is selected explicitly. Chat Completions sends
`POST {base_url}/chat/completions`; Responses sends `POST {base_url}/responses`.
The selected provider is used by `/ask`; provider errors fail closed and never
fall back to Ollama. `Test Connection` sends a minimal real generation request
for the selected model and protocol and may incur a small provider usage charge.

The page displays the effective configuration and its source (`DEFAULT`, `ENV`,
or `LOCAL_UI_OVERRIDE`). Embedding and retrieval values are read-only. The
`PUBLIC_ONLY` source-class gate is read-only and enforced by the service.

## Safe restart

The Compose service sets `PKR_RESTART_STRATEGY=supervised_process_exit` and is
published only on the host loopback address. When a save requires a restart,
the page offers **Restart Service**. The service returns a restart ID first,
then exits its own Uvicorn process gracefully; Compose's existing
`restart: unless-stopped` policy starts it again. The page waits up to 60
seconds, reconnects to `/health`, reloads configuration, and reports success
only after the saved provider, model, endpoint, protocol, credential status,
and apply state match.

Direct or otherwise unsupervised starts default to `RESTART_UNAVAILABLE` and
cannot call the restart endpoint. The endpoint accepts no command or process
identifier, only works on a loopback Host, and sends SIGTERM to the current
service process. The application never calls Docker or executes a shell
command. Do not expose this unauthenticated admin page outside loopback.

## Save and apply

The settings page requires successful validation and a successful provider/model
identity test for the exact edited values before accepting a save. Values are
written atomically to `PKR_UI_CONFIG_PATH`, or by default to
`$PKR_DATA_DIR/config.local.json`. The local file is ignored by Git. It contains
only non-secret settings. If the service's startup-bound provider instance does
not match the saved values, the page reports `RESTART_REQUIRED` and offers the
supervised one-click restart when available.

Environment configuration remains supported. Local UI overrides take
precedence over environment and defaults. Removing a value from the local
override file lets the environment/default layer take effect again.

## Secret handling

Ollama does not require a credential. The OpenAI-compatible API key is write-only:
GET returns only `CONFIGURED` / `MISSING`, and the key never enters
`config.local.json`, the configuration hash, logs, fixtures, evidence, or Git.
The Settings page stores a supplied key in a separate `secrets.local.json` file
with mode `0600`; `OPENAI_COMPATIBLE_API_KEY` remains supported as an environment
fallback. Provider URLs containing userinfo, query strings, or fragments are
rejected. Provider connection failures return redacted messages.
