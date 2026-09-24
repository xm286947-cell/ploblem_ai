# HARDWARE-CASE-KNOWLEDGE-ADAPTER-001

Status: IMPLEMENTED_PENDING_CI

## Frozen Knowledge dependency

- Capability: `KNOWLEDGE_CAPABILITY_RELEASE_V0.3`
- Capability source commit: `92ef3f3c4ec80c4987f2b715dcd2d485eda8e372`
- Knowledge main merge commit: `1ed6dc186637049afaa98c28ea2c8042c63588ae`
- Capability package SHA256:
  `2e73cf0cf10a92a33378f72546e76204918ff756c1afc466d4de017c67933550`

Contracts:

- `knowledge-candidate/v1`
- `knowledge-evidence/v1`
- `knowledge-review/v1`
- `knowledge-publish/v1`
- `knowledge-object/v1`
- `knowledge-query/v1`

## Architecture

```text
Hardware Case Domain
        |
        v
HardwareCaseKnowledgeAdapter
        |
        v
Knowledge Public HTTP Contract
        |
        v
KNOWLEDGE_CAPABILITY_RELEASE_V0.3
```

The adapter imports no Knowledge repository, DB, private service, Runtime, or
Provider implementation.

## Ownership boundary

Hardware Case continues to own:

- Hardware schema and AI structured content
- Evidence grounding against the original Word
- human review semantics in the Hardware workbench
- dual-tree semantics and mapping confirmation
- Hardware Publish Gate
- P01-P07
- Runtime Adapter and Prompt

Knowledge owns only the public Candidate/Evidence/Review/Publish/Object/Query
mechanics.

## Publish ordering

```text
Hardware review + Evidence + confirmed mapping
        |
        v
Hardware Publish Gate
        |
        | PASS only
        v
knowledge-publish/v1
```

A failed Hardware gate never calls Knowledge Publish.

## Release version vs data release version

`KNOWLEDGE_CAPABILITY_RELEASE_V0.3` is the frozen capability package.
`knowledge_release_version` passed to the adapter is the immutable data
release used by `knowledge-query/v1`. The adapter never silently substitutes
one for the other.

## Failure semantics

The adapter preserves public Knowledge error codes and adds explicit local
boundary errors for:

- `KNOWLEDGE_UNAVAILABLE`
- `HARDWARE_PUBLISH_GATE_NOT_PASSED`
- `KNOWLEDGE_REVISION_MISMATCH`
- contract/domain/object-type mismatches

There is no silent fallback to Hardware local DB when Knowledge query fails.
