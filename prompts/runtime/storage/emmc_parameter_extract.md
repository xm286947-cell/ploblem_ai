# Storage eMMC Parameter Extraction

This prompt is owned by the Storage domain. Runtime owns provider execution,
retry, budget, secret injection, checkpoint and resume.

Extract the fields listed in `required_fields` from the supplied source text.

Return **only** a strict JSON array. Each array item must conform to the
configured `StorageFieldResult` schema:

- `field_id`: one requested field id.
- `status`: `FOUND`, `MISSING`, or `CONFLICT`.
- `normalized_value`: normalized value when found.
- `unit`: normalized unit when applicable.
- `evidence`: an array; use an empty array when the input does not provide
  structured evidence references.
- Do not wrap JSON in Markdown fences.
- Do not invent extra field ids.
- For a clearly stated eMMC P/E cycle value, normalize the numeric value and
  use unit `cycles`.

Invalid JSON/schema or truncated output must be surfaced to Runtime so Runtime,
not business code, owns retry.
