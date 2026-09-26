# RCM_COMPATIBILITY_MATRIX_V1.0

Status: **UPDATED** for `RCM-R2-MAJOR-KNOWLEDGE-PUBLICATION-BINDING-001`

| RCM row | Contract / capability | Status | Pinned evidence |
|---|---|---|---|
| R01 | `source-problem-itr-ref/v1` | PASS | Existing R1 public-ref regression |
| R02 | `major-problem-context/v1` | PASS | Existing Canonical Major Context regression |
| R03 | Major -> Knowledge publication | PASS | `major-knowledge-publication/v1`, P01-P10 |
| R04 | `common-evidence/v1.0` | PASS | Public Evidence projection in this contract |
| R05 | `knowledge-candidate/v1` intake | PASS | Existing KP-D01 service through receiving port |
| R06 | `knowledge-object/v1` publish | PASS | Existing KP-D03 review/publish pipeline |
| R07 | `knowledge-release-binding/v1.0` | PASS | `KP-STORAGE-RC1-VALIDATION-001` pin |

R03 is no longer a draft or gap. The Major producer does not import Knowledge
repositories or internal Knowledge models. The receiving adapter translates the
public contract into the existing intake service; review, publish, version,
and release ownership remain in Unified Knowledge.
