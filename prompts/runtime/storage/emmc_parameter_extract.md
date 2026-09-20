# Storage eMMC Parameter Extraction

This prompt is owned by the Storage domain adapter. The Runtime treats this
file only as a versioned prompt reference and freezes its content hash into the
execution snapshot.

Return output that conforms to the configured StorageFieldResult schema. When
the provider response is truncated or invalid JSON/schema, the adapter must
raise the Runtime standard validation error so retry remains Runtime-owned.
