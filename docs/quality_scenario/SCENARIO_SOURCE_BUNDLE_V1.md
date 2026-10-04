# ScenarioSourceBundle V1 (Wave 1)

Contract identifier: `scenario-source-bundle/v1`.

The bundle is a read-only, immutable snapshot of existing source-material
records. `SOFTWARE_ASSESSMENT` is the required and sole production root;
`RESOLUTION`, `ITR`, and `MISSED_TEST` are supporting evidence only. Missing
supporting sources are represented explicitly and do not imply a negative
finding. Multiple current records in different source groups are marked
`CONFLICT` instead of being selected implicitly.

## Contract fields

- `bundle_id`: stable identity derived from the selected software-assessment
  material ID.
- `bundle_revision`: SHA-256 over contract version, each source ID/revision,
  source version, trigger source, and trigger reason.
- `primary_source_type` / `primary_source_id`: always the selected
  `SOFTWARE_ASSESSMENT` source.
- `authority`: field-family ownership; supporting sources cannot replace the
  assessment's business identity, selection scope, assessment facts, or issue
  description.
- `sources`: per-source `PRESENT`, `MISSING`, or `CONFLICT` status with source
  IDs, revisions, versions, business keys, and source groups.
- `field_values` / `field_evidence`: allowlisted raw source fields with exact
  `source_type`, `source_id`, `source_revision`, `source_field`, and authority.
- `missing_information` / `warnings`: explicit absence and unresolved source
  conflicts.

Snapshots are appended to the existing mature source database in
`scenario_source_bundle_v1_snapshot`, keyed by bundle ID and revision. Database
triggers reject updates/deletes; a duplicate insert is idempotent only when the
stored canonical JSON is byte-identical. A source revision or trigger change
creates a new revision and retains the earlier snapshot.

Wave 1 does not invoke Reverse Quality, create QSV1 candidates, add a second
business master, or write to legacy quality-scenario tables. Those behaviors
belong to later waves.
