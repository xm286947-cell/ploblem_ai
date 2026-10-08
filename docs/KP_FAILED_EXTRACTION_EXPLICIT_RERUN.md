# KP explicit rerun of a FAILED extraction Task

Scope: internal operator use only. This **does not** add an unauthenticated HTTP rerun endpoint, re-enable automatic retries, or allow mutation of the original Source/Revision/Topics.

## Why an ordinary replay does not work

`KnowledgeExtractionService.extract()` uses a stable logical Runtime `request_id` made from the Source identity, original content hash and normalized requested topics. The Runtime intentionally returns the prior Task for the same request ID. A terminal FAILED Task is not resumed in place.

## Supported operator-controlled rerun

1. Keep the original frozen Source and structured document unchanged.
2. Diagnose/fix Provider configuration and pass a *stateless* Provider contract probe. A TCP/credential-presence check alone is not sufficient.
3. In the **isolated candidate data copy**, locate the previous **FAILED** Runtime Task ID and request ID by read-only Runtime inspection. Verify that the request ID corresponds to the expected source/revision/content/topics.
4. Obtain a documented, authenticated operator approval **outside** this library. `ExtractionRerunApproval` carries audit facts but is **not an authentication mechanism**. Caller must enforce authorization before invoking this internal service.
5. Call `knowledge_product.extract_source` internally with `rerun_approval` using the same source/revision/topics. Do **not** expose these arguments to the public Storage HTTP form.

Example for an authorized internal caller (illustrative; not executed by CI):

```python
from knowledge_production import ExtractionRerunApproval
from products.storage_rc1.storage_life import knowledge_product

result = knowledge_product.extract_source(
    source_id="<existing-source-id>",
    source_version="<existing-revision>",
    requested_topics=[],  # EXACT same normalized topics as failed predecessor
    rerun_approval=ExtractionRerunApproval(
        generation=1,
        previous_task_id="<verified-failed-task-id>",
        approved_by="<authenticated-operator>",
        approval_ref="<approved-change-ticket>",
        reason="Provider configuration repaired and contract probe passed",
    ),
)
```

The caller's process must point to the *isolated* repository copy and approved external model configuration using the existing `STORAGE_KNOWLEDGE_REPOSITORY_DIR` and `STORAGE_MODEL_CONFIG` environment bindings. Do not print/copy the configuration body or any credential.

### Identity and lineage contract

- Generation 0 (no approval): original request ID and idempotency behaviour unchanged.
- Generation 1: `<logical-request-id>:rerun:1`; predecessor must be **the original FAILED Task**.
- Generation N>1: `<logical-request-id>:rerun:N`; predecessor must be generation N-1 in **FAILED** status.
- Missing approval details, a wrong predecessor, mismatched topics or a non-FAILED predecessor fail closed before dispatch.
- **Persisted input-hash preflight:** before `runtime.invoke`, hash the new `AgentRequest.input` using the same canonical serializer as Runtime and compare with the predecessor's persisted `TaskRecord.input_hash`. Different page text, section or source anchor under an otherwise identical Source/Revision/Topics identity raises `EXTRACTION_RERUN_INPUT_MISMATCH`; no new Task or provider request is created.
- Same generation and same approval is an idempotent replay, not a new Provider call.
- Different approval details for an existing generation trigger Runtime fingerprint conflict.
- Original failed Task is preserved. New Task metadata records logical request ID, generation, predecessor Task/Request IDs, and operator-supplied reason/approval reference.
- Source bytes, Source revision, input and evidence validation remain unchanged.
- Existing deterministic Candidate/Evidence identities remain in force; conflicts fail closed. No automated Review, Publish or Formal Release.

## Trusted internal caller: required authorization evidence

The `ExtractionRerunApproval` dataclass only transports *claimed* operator and ticket references. It is **not an authenticator**, and copying this object does not authorize a Provider call. PR #562 leaves the public Storage HTTP form unchanged: rerun can only be invoked from an internal application process with permission to execute code in the isolated environment. A public authenticated rerun API is not part of this change.

The operational owner **must** produce an auditable, redacted record from a trusted, access-controlled operator runner **before** approval can be accepted for a real Extraction. A self-reported string inside the approval object does not count.

Minimum reviewer-verifiable authorization evidence:

1. **Authenticated caller principal** from an independent identity/OS/session audit record, with a timestamp and operator action; do not infer identity from `approved_by`.
2. **Authorization basis** (role/ACL/grant and a valid change or approval ticket), checked by the runner against an authoritative system; record status and immutable ticket/reference, not credentials.
3. **Isolated execution boundary**: executable/runner identity and version, host/environment identity, repository/Runtime DB copy, scoped filesystem permissions, and explicit proof that the public HTTP request cannot include a rerun approval.
4. **Approved request binding**: exact Source ID, Revision, source content hash, normalized topics, logical request ID, predecessor FAILED Task ID, persisted input hash, and authorized generation.
5. **One-call budget and audit**: record the approved provider-call allowance, immutable run/audit ID, outcome and whether a new Task was created. Never log the API key, Authorization header or provider configuration body.

**Security Gate stays `LIMITED_TO_AUTHORIZED_INTERNAL_CALL` unless an independent reviewer has verified actual trusted-runner/approval evidence.** This document is a required evidence contract, **not evidence that the control was exercised**. If no trusted caller or approval verification exists for the environment, keep #475 blocked; do not invoke through an ad hoc interactive shell.

## Operational gates

1. Run the focused KP regression and CI on a new branch.
2. Review and merge. Rebuild/revalidate an **isolated** Storage Candidate from the merged source; never overlay changes into frozen package ZIP or live `:18770/:18772`.
3. Confirm existing historical FAILED Task still exists in the isolated Runtime DB, and confirm approved Provider config and contract probe.
4. Explicitly authorize a **new** generation/Provider-call budget and invoke it once.
5. After Extraction succeeds, perform Evidence/Evaluation, stop for **human review** and only then consider Publish/Release.
6. Run #473 EG01–EG07 only after #475 Formal NAND Knowledge Release is verified.

If any precondition is not provable, report BLOCKED rather than changing Source/Revision/topics, deleting Runtime records, using an invented Candidate, or bypassing human Review.
