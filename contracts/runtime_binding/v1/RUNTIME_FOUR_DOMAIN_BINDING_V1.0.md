# Runtime Four-domain Binding v1.0

This contract is the admission boundary for business domains that consume the
Unified Agent Runtime. It binds each domain to a globally unique Agent ID, one
canonical Agent YAML, and a domain adapter. The Runtime remains the only owner
of provider calls, retry and budget policy, secret resolution, and execution
snapshots; adapters own only domain input/output semantics.

The frozen domain set is:

| Domain | Agent | Canonical config |
| --- | --- | --- |
| `STORAGE` | `storage.emmc.parameter_extract` | `config/runtime/agents/storage.emmc.parameter_extract.yaml` |
| `MAJOR_ISSUE` | `major_issue.v2.occurrence` | `config/runtime/agents/major_issue.v2.occurrence.yaml` |
| `REVERSE_QUALITY` | `reverse_quality.analysis` | `config/runtime/agents/reverse_quality.analysis.yaml` |
| `KNOWLEDGE` | `knowledge.production.extract` | `config/runtime/agents/knowledge.production.extract.yaml` |

The machine-readable source is `runtime_binding.json`. Consumers must load the
manifest through `runtime.binding.validate_runtime_binding`; a missing domain,
duplicate Agent ID, missing config, or changed ownership flag fails closed.

This is a binding and compatibility contract. It does not create a second
Provider, Runtime, retry loop, secret store, or domain database. Existing
legacy adapters may remain behind the bound adapter while they are migrated;
they must not bypass the canonical Runtime entry for new execution paths.
