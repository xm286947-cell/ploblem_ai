# ScenarioSourceBundle V1 (Wave 1)

Contract identifier: `scenario-source-bundle/v1`.

The bundle is built only from an already-frozen Software Assessment Source
Snapshot. The snapshot's selected issue and source references are the binding
truth. The database is used only to locate those exact frozen material IDs and
read their raw evidence; the builder does not search by canonical ITR, re-run
joins, or choose relationships. A missing locator or changed source revision
fails closed. `SOFTWARE_ASSESSMENT` is the required and sole selection root;
`RESOLUTION`, `ITR`, and `MISSED_TEST` are supporting sources. Missing sources
are represented explicitly and do not imply a negative finding.

## Contract fields

- `bundle_id`: stable identity derived from the selected software-assessment
  material ID.
- `bundle_revision`: SHA-256 over contract version, each source ID/revision,
  source version, trigger source, and trigger reason.
- `selected_issue`: frozen knowledge/business issue identity, selected
  assessment record and revision, product/customer/org scope, and KPI period.
- `sources[]`: exact references frozen by the source selector, with source type,
  ID/revision, relation type, authority fields, locator, and capture time.
- `source_status`: explicit per-type `PRESENT`, `MISSING`, or `CONFLICT` state.
- `authority`: frozen field matrix: assessment owns selection/scope; problem,
  product, customer, IPMT and SPDT prefer Resolution, then Assessment, then ITR;
  root cause/corrective actions prefer Resolution; missed-test facts come from
  effective missed-test analysis. A field-matched human confirmation outranks
  its AI inference, and conflicts remain visible.
- `facts` / `field_evidence`: normalized projections with exact `source_type`,
  `source_id`, `source_revision`, `source_field`, evidence ID, and provenance.
- `effective_analysis`: the frozen latest occurrence, escape, recurrence and
  capability-gap results, each carrying analysis revision/status, issue
  version/input provenance, and associated human confirmations.
- `missing_information` / `warnings`: explicit absence and unresolved source
  conflicts.

Source-snapshot metadata records the snapshot ID, capture time, builder,
selected issue/source counts, and material/analysis revisions. Snapshots are
appended to the existing mature source database in
`scenario_source_bundle_v1_snapshot`, keyed by bundle ID and revision. Database
triggers reject updates/deletes; a duplicate insert is idempotent only when the
stored canonical JSON is byte-identical. A source revision or trigger change
creates a new revision and retains the earlier snapshot.

Wave 1 does not invoke Reverse Quality, create QSV1 candidates, add a second
business master, or write to legacy quality-scenario tables. Those behaviors
belong to later waves.
