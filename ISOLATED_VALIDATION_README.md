# W4 isolated Public Knowledge validation

This mode validates a newer Public Knowledge build without stopping, replacing,
or modifying existing Public Knowledge containers or source volumes.

Each start creates a unique Compose project, container, cloned data volume, and
a durable per-instance state record. Existing isolated instances are never
deleted or replaced on startup. If port 19000 is already in use, the launcher
automatically selects the next free port in 19000..19049.

State is stored under:

```
.pkr_isolated_instances/<instance_id>.env
```

`.pkr_isolated_instances/LATEST` is only a convenience pointer. It can change
on later starts, but it never replaces any per-instance lifecycle record.

Run passive validation:

```bash
bash scripts/start_isolated.sh
```

List every recorded isolated instance:

```bash
bash scripts/list_isolated.sh
```

Stop one instance explicitly:

```bash
bash scripts/stop_isolated.sh <instance_id>
```

If only one running instance exists, `bash scripts/stop_isolated.sh` may be
used without an ID. If multiple instances are running, the command fails closed
and asks for an explicit instance ID.

Stop all recorded running isolated instances:

```bash
PKR_STOP_ALL_ISOLATED=1 bash scripts/stop_isolated.sh
```

Volumes are retained by default for inspection. To remove the selected
instance's cloned volume while stopping:

```bash
PKR_REMOVE_ISOLATED_VOLUME=1 bash scripts/stop_isolated.sh <instance_id>
```

The existing Public Knowledge source volume is always mounted read-only into a
short-lived copy helper; the original container and original volume are not
stopped, removed, or written.

No real generation-provider request is made by default. To authorize exactly
one provider-health probe for a newly created instance:

```bash
PKR_ALLOW_REAL_PROVIDER_TEST=1 bash scripts/start_isolated.sh
```

If source-volume auto-detection is ambiguous:

```bash
PKR_SOURCE_VOLUME=<existing-volume-name> bash scripts/start_isolated.sh
```

To request a specific free port:

```bash
PKR_ISOLATED_PORT=19010 bash scripts/start_isolated.sh
```

If the requested port is occupied, startup fails closed and does not replace
anything.
