# W4 isolated Public Knowledge validation

This mode validates a newer Public Knowledge build without stopping, replacing,
or modifying existing Public Knowledge containers or source volumes.

Each start creates a unique Compose project, container, and cloned data volume.
It never deletes an existing isolated instance. If port 19000 is already in
use, the launcher automatically selects the next free port in 19000..19049.

The existing Public Knowledge volume is mounted read-only into a short-lived
copy helper and cloned into the run-specific volume. Saved config, source
catalog, and local SecretStore are therefore available in the isolated copy
without modifying the source volume.

Run passive validation:

```bash
bash scripts/start_isolated.sh
```

The script writes the created resource identities and selected port to
`.pkr_isolated_last.env`. No real generation-provider request is made by
default.

Run exactly one real provider-health probe only when explicitly authorized:

```bash
PKR_ALLOW_REAL_PROVIDER_TEST=1 bash scripts/start_isolated.sh
```

Stop only the last recorded isolated instance:

```bash
bash scripts/stop_isolated.sh
```

The cloned volume is retained by default. Remove only that recorded isolated
volume with:

```bash
PKR_REMOVE_ISOLATED_VOLUME=1 bash scripts/stop_isolated.sh
```

If source-volume auto-detection is ambiguous:

```bash
PKR_SOURCE_VOLUME=<existing-volume-name> bash scripts/start_isolated.sh
```

To request a specific free port:

```bash
PKR_ISOLATED_PORT=19010 bash scripts/start_isolated.sh
```

If the requested port is already occupied, startup fails closed and does not
replace anything.
