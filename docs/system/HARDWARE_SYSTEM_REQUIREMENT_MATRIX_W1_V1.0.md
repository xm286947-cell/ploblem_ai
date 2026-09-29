# Hardware System Requirement Matrix — W1 V1.0

TASK=HARDWARE-SYSTEM-REQUIREMENTS-W1-001
BASELINE_MAIN=633224cbcc8f91e63a62226c8720df76c9758f49
STATUS=FROZEN

## Capability preservation matrix

| Capability | System requirement binding | State | W2/W3 action |
|---|---|---|---|
| HC-01 Word case import | SYS-FUN-001 | PRESERVED | None |
| HC-02 AI Structure / Candidate | SYS-FUN-002/003, SYS-AI-001/004 | PRESERVED | Add trace architecture only |
| HC-03 Evidence | SYS-FUN-005, SYS-DATA-004 | PRESERVED | Observability/recovery only |
| HC-04 Source Trace | SYS-FUN-005, SYS-SEC-002 | PRESERVED | Source boundary hardening only |
| HC-05 Human Review | SYS-FUN-004 | PRESERVED | IAM architecture only |
| HC-06 Circuit Tree | SYS-FUN-006 | PRESERVED | No rebuild |
| HC-07 Device Tree | SYS-FUN-006 | PRESERVED | No rebuild |
| HC-08 Excel Tree Import | SYS-FUN-007, SYS-DATA-005/006 | PRESERVED | Recovery/upgrade architecture only |
| HC-09 Dual-Tree Mapping | SYS-FUN-008/009 | PRESERVED | No rebuild |
| HC-10 Publish | SYS-FUN-009/012 | PRESERVED | Failure/recovery architecture only |
| HC-11 Search | SYS-FUN-010/011 | PRESERVED | Performance budget only |
| HC-12 Case Detail | SYS-FUN-010/011 | PRESERVED | Performance budget only |
| HC-13 Public Ref -> Publication -> Evidence | SYS-FUN-012/013 | PRESERVED | Public binding/version architecture |
| HC-14 Fail-Closed semantics | Section 7, SYS-REL-003/004 | PRESERVED | Must not weaken |

CAPABILITY_TOTAL=14
CAPABILITY_PRESERVED=14
PRODUCT_FEATURE_GAP=0

## W2 architecture gap register

| Gap ID | Topic | Current state | Required W2 decision | W3 code expected? |
|---|---|---|---|---|
| W2-G01 | Public consumer deployment binding | Contract exists; external binding not architecture-frozen | Choose stable HTTP/deep-link integration boundary and version strategy | POSSIBLE_MINIMAL |
| W2-G02 | Contract version compatibility | v1 frozen; evolution policy not frozen | Compatibility/deprecation rules | POSSIBLE_MINIMAL |
| W2-G03 | Windows/macOS packaging parity | BAT + POSIX SH exist | Define packaging/entry parity and macOS UX | POSSIBLE_MINIMAL |
| W2-G04 | Schema migration | Product DB exists; upgrade policy not frozen | Migration/version/rollback strategy | LIKELY |
| W2-G05 | Backup/restore | Not system-baselined | Scope, ownership, restore ordering | POSSIBLE |
| W2-G06 | IAM | MVP role semantics exist | Production authn/authz boundary | LIKELY_LATER |
| W2-G07 | Health/readiness | Startup check exists | Dependency health model | LIKELY |
| W2-G08 | Observability/correlation | Partial component diagnostics | Trace IDs/log model/diagnostic export | LIKELY |
| W2-G09 | Failure/recovery | Several fail-closed rules exist | End-to-end failure-state matrix and recovery | POSSIBLE |
| W2-G10 | Config/secrets lifecycle | Local config policy exists | Source/priority/rotation/ownership | POSSIBLE |
| W2-G11 | Performance/capacity | No approved numeric SLO | Freeze measurable budgets | TEST/ARCH |
| W2-G12 | Release compatibility | Independent baseline active | Forward-integration/rebase + consumer compatibility policy | PROCESS/ARCH |

## W2 forbidden redesign

W2 shall not:
- rebuild the 14 capabilities;
- introduce a second Runtime;
- introduce a second Knowledge service;
- introduce a second Web host/port;
- move Hardware implementation ownership into Overall;
- weaken fail-closed semantics;
- replace confirmed-only consumer semantics with AI candidate data;
- treat test-evidence remediation as a product defect without evidence.

## W3 entry criteria

W3 implementation may start only after W2:
- approves component/deployment topology;
- resolves G01/G03/G04/G07/G08 at minimum;
- separates MUST_IMPLEMENT from LATER/PILOT items;
- defines regression impact;
- keeps 14/14 preservation gate mandatory.

W1_GATE=PASS
NEXT=W2_ARCHITECTURE_BASELINE
