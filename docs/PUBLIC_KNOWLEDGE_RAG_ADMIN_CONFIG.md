# Public Knowledge RAG local configuration page

Open `http://127.0.0.1:8080/settings` after starting the Public Knowledge service.
The Docker Compose port remains bound to loopback by default.

## Provider configuration

The page edits only the currently supported Ollama generation provider:

- `GENERATION_PROVIDER`
- `OLLAMA_URL`
- `OLLAMA_MODEL`
- `OLLAMA_MODEL_DIGEST`
- `OLLAMA_TIMEOUT_SECONDS`
- `OLLAMA_NUM_PREDICT`
- `OLLAMA_THINKING_MODE`

The page displays the effective configuration and its source (`DEFAULT`, `ENV`,
or `LOCAL_UI_OVERRIDE`). Embedding and retrieval values are read-only. The
`PUBLIC_ONLY` source-class gate is read-only and enforced by the service.

## Save and apply

The settings page requires successful validation and a successful provider/model
identity test for the exact edited values before accepting a save. Values are
written atomically to `PKR_UI_CONFIG_PATH`, or by default to
`$PKR_DATA_DIR/config.local.json`. The local file is ignored by Git. It contains
only non-secret settings. If the service's startup-bound provider instance does
not match the saved values, the page reports `RESTART_REQUIRED`; it never claims
that the live provider has already changed.

Environment configuration remains supported. Local UI overrides take
precedence over environment and defaults. Removing a value from the local
override file lets the environment/default layer take effect again.

## Secret handling

This service's current LAN Ollama provider does not require a credential. No
secret field is accepted or returned by the admin API. Provider URLs containing
userinfo, query strings, or fragments are rejected to prevent credentials from
being stored in the local override file. Provider connection failures are
returned as generic redacted messages.
