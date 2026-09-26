# Runtime Four-domain Binding v1.0

This contract is the admission boundary for business domains that consume the
Unified Agent Runtime. It binds each domain to a globally unique Agent ID, one
canonical Agent YAML, and a domain adapter. The Runtime remains the only owner
of provider calls, retry and budget policy, secret resolution, and execution
snapshots; adapters own only domain input/output semantics.

The frozen RCM-R6 business-domain set is:

| Domain | Agent | Canonical config |
| --- | --- | --- |
| `MAJOR_ISSUE` | `major_issue.v2.occurrence` | `config/runtime/agents/major_issue.v2.occurrence.yaml` |
| `HARDWARE_CASE` | `hardware_case.structure` | `config/runtime/agents/hardware_case.structure.yaml` |
| `REVERSE_QUALITY` | `reverse_quality.analysis` | `config/runtime/agents/reverse_quality.analysis.yaml` |
| `STORAGE` | `storage.emmc.parameter_extract` | `config/runtime/agents/storage.emmc.parameter_extract.yaml` |

`KNOWLEDGE` is a shared capability consumer, retained under the manifest's
`shared_capabilities` extension and excluded from the four-business-domain
count.

The public Runtime contract baseline is `P0.2_CONTRACT_FROZEN_V1.0`. The
Runtime implementation level is recorded separately as `P0.3`; the latter is
not a public contract version.

The machine-readable source is `runtime_binding.json`. Consumers must load the
manifest through `runtime.binding.validate_runtime_binding`; a missing domain,
duplicate Agent ID, missing config, or changed ownership flag fails closed.

This is a binding and compatibility contract. It does not create a second
Provider, Runtime, retry loop, secret store, or domain database. Existing
legacy adapters may remain behind the bound adapter while they are migrated;
they must not bypass the canonical Runtime entry for new execution paths.
