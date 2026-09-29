# Hardware Independent Baseline Capability Ledger

TASK=HARDWARE-KNOWLEDGE-INDEPENDENT-DEV-REBASE-001
STATUS=HARDWARE_INDEPENDENT_BASELINE_ACTIVE
BASE_MAIN=86e5e21e16e33c81587260eb43eb3cf85994e02d
BASELINE_POLICY=Preserve mature Hardware capabilities; isolate development ownership from Overall; reuse platform Runtime, Knowledge and Public Contracts.

## Architecture boundary
- Hardware is an independently developable product domain.
- Overall is a consumer/integration shell, not the source of Hardware implementation.
- Unified Runtime remains shared; no Hardware-local Runtime fork.
- Unified Knowledge remains shared through frozen public contracts; no Hardware-local Knowledge fork.
- Hardware-only composition is the isolation contract; Overall routes/services are not prerequisites for Hardware execution.
- #200 is TEST_EVIDENCE_REMEDIATION (PRODUCT_DEFECT=NO, DEV_RETURN=NO) and is not a development blocker.

## Capability ledger
| ID | Capability | State | Canonical implementation/evidence |
|---|---|---|---|
| HC-01 | Word case import | PRESERVED | services/hardware_case_intake.py; services/hardware_case_word.py |
| HC-02 | AI Structure / Candidate | PRESERVED | services/hardware_case_ai_adapter.py; config/runtime/agents/hardware_case.structure.yaml |
| HC-03 | Evidence | PRESERVED | services/hardware_case_backend.py; tests/test_hardware_case_evidence_source.py |
| HC-04 | Source Trace | PRESERVED | services/hardware_case_source_store.py; evidence source APIs |
| HC-05 | Human Review | PRESERVED | review_case contract/backend + hardware_case_review UI |
| HC-06 | Circuit Tree | PRESERVED | hardware case/tree repositories + CIRCUIT_FEATURE contract |
| HC-07 | Device Tree | PRESERVED | hardware case/tree repositories + MATERIAL_DEVICE contract |
| HC-08 | Excel Tree Import | PRESERVED | services/hardware_tree_excel.py; hardware_tree_import_* |
| HC-09 | Dual-Tree Mapping | PRESERVED | mapping contract/backend + grounded intake proposals |
| HC-10 | Publish | PRESERVED | HardwareCaseBackendService.publish_case publish gate |
| HC-11 | Search | PRESERVED | /api/v2/hardware-cases search consumer path |
| HC-12 | Case Detail | PRESERVED | /api/v2/hardware-cases/{case_id} + detail UI |
| HC-13 | RCM-R3 Public Ref -> Publication -> Evidence | PRESERVED | public-contract/knowledge adapter chain retained from canonical main |
| HC-14 | Closed-P0 Fail-Closed semantics | PRESERVED | unresolved tree validation, missing mapping, evidence/source integrity and publish gates |

CAPABILITY_TOTAL=14
CAPABILITY_PRESERVED=14
CAPABILITY_MISSING=0

## Reuse
RUNTIME_REUSE=YES
KNOWLEDGE_REUSE=YES
PUBLIC_CONTRACT_REUSE=YES

## Coupling
OVERALL_COUPLING=ISOLATED
The existing hardware-only composition regression proves Hardware can boot without loading Overall/Repeat/Major business services. Overall may mount/consume Hardware, but Hardware development must not import Overall as its implementation source.

## Regression scope for this baseline
Required focused suites:
- tests/test_hardware_case_domain_boundary.py
- tests/test_hardware_case_contract.py
- tests/test_hardware_case_backend.py
- tests/test_hardware_case_ai_adapter.py
- tests/test_hardware_case_runtime_adapter.py
- tests/test_hardware_case_knowledge_adapter.py
- tests/test_hardware_case_evidence_source.py
- tests/test_hardware_case_product_api_e2e.py
- tests/test_hardware_tree_import_m1.py
- tests/test_hardware_tree_import_m2.py
- tests/test_hardware_tree_import_m3a_api.py
- tests/test_hardware_tree_import_m3b_frontend.py

## Development gap
OPEN_P0_DEV_GAP=0
OPEN_P1_DEV_GAP=1

P1-01: formalize the Hardware Public Contract consumed by Overall as the only cross-product integration boundary and keep the independent branch continuously rebased/forward-integrated from canonical main without importing Overall-owned implementation.

Evidence remediation in #200 remains test-owned and does not enter OPEN_P0_DEV_GAP.
