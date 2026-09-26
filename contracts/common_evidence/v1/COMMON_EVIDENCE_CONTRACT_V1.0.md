# COMMON_EVIDENCE_CONTRACT_V1.0

Status: **FROZEN** after RCM-R4.

`common-evidence/v1.0` is the only cross-domain Evidence representation. It is a transport contract, not a shared database model. A producer may retain any richer internal Evidence object, but a consumer receives only this projection through the producer's domain adapter.

## Contract

Every object contains the following keys:

```json
{
  "contract_version": "common-evidence/v1.0",
  "evidence_id": "EVD-001",
  "evidence_type": "SOURCE_EXCERPT",
  "source": {
    "source_type": "PDF",
    "source_id": "NVME",
    "source_version": "2.0d"
  },
  "locator": {"page": 200, "section": "SMART / Health", "anchor": "page:200"},
  "excerpt": "verbatim excerpt when available",
  "source_text": null,
  "content_hash": "sha256...",
  "source_ref": "NVME@2.0d",
  "source_reference": "https://nvmexpress.org/specification",
  "producer_domain": "Storage",
  "producer_object_id": "KO-001",
  "producer_object_version": 1,
  "verification_status": "HUMAN_CONFIRMED",
  "evidence_status": "BOUND",
  "created_at": "2026-09-24T03:32:58Z"
}
```

`page`, `section`, `anchor`, `source_text`, URLs, hashes, and version values are nullable because absence is a fact. Adapters must preserve `null`; they must never infer a page, fabricate a URL, or turn an internal path into a public source reference. `source_reference` is the producer-owned source URI/reference when one exists. `source_ref` is the stable source identity used by the release manifest.

The normative machine-readable schema is `common_evidence_contract.schema.json` in this directory.

## Ownership and traceability

The producer remains authoritative for the original Evidence and source. The trace is:

`producer object → common Evidence → source identity/reference → immutable release snapshot`.

Consumers must not call a producer database, repository, internal model, table, vector index, or JSON directory. The only supported boundary is an adapter that returns this contract.
