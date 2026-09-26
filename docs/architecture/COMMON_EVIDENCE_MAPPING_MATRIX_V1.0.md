# COMMON_EVIDENCE_MAPPING_MATRIX_V1.0

Status: **FROZEN** (RCM-R4)

| Producer evidence | Adapter | `producer_domain` | Source ownership |
| --- | --- | --- | --- |
| External Source / Knowledge Production evidence | `map_external_source_evidence` | `EXTERNAL_SOURCE` | Knowledge Production source document |
| Business Object evidence | `map_business_object_evidence` | `BUSINESS_OBJECT` | Owning business domain |
| Historical Case evidence | `map_historical_case_evidence` | `HISTORICAL_CASE` | Historical Case |
| Major Issue / Repeat Risk evidence | `map_major_issue_evidence` | `MAJOR_ISSUE` | Major Issue / Repeat Risk |
| Hardware Case evidence | `map_hardware_case_evidence` | `HARDWARE_CASE` | Hardware Case |
| Quality Scenario evidence | `map_quality_scenario_evidence` | `QUALITY_SCENARIO` | Quality Scenario |

Every adapter emits the same `CommonEvidence` shape.  Domain-specific fields
remain in the producer object and may be retained in producer storage; they are
not added to the common public contract.  The mapping is pure and does not
open producer repositories, databases, or private models.
