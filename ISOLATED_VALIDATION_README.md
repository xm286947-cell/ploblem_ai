# W4 isolated Public Knowledge validation

This mode is for validating a newer Public Knowledge build without touching the
existing `storage-public-knowledge` container or its persistent volume.

It creates only:
- container: `storage-public-knowledge-w4-isolated`
- volume: `storage_public_knowledge_w4_isolated_data`
- port: `127.0.0.1:19000`

The existing Public Knowledge volume is mounted read-only into a short-lived
copy helper and cloned into the isolated volume, so the saved non-secret
configuration, source catalog, and local SecretStore remain available to the
isolated service without modifying the original volume.

Run passive validation only:

```bash
bash scripts/start_isolated.sh
```

This checks the new route exists and reports the persisted provider/model. It
does not call the configured generation provider.

Run exactly one real provider-health probe only when explicitly authorized:

```bash
PKR_ALLOW_REAL_PROVIDER_TEST=1 bash scripts/start_isolated.sh
```

Stop isolated service:

```bash
bash scripts/stop_isolated.sh
```

The isolated volume is retained by default. Remove only that isolated copy with:

```bash
PKR_REMOVE_ISOLATED_VOLUME=1 bash scripts/stop_isolated.sh
```

If auto-detection cannot find the original Public Knowledge volume, set:

```bash
PKR_SOURCE_VOLUME=<existing-volume-name> bash scripts/start_isolated.sh
```
