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
- Same generation and same approval is an idempotent replay, not a new Provider call.
- Different approval details for an existing generation trigger Runtime fingerprint conflict.
- Original failed Task is preserved. New Task metadata records logical request ID, generation, predecessor Task/Request IDs, and operator-supplied reason/approval reference.
- Source bytes, Source revision, input and evidence validation remain unchanged.
- Existing deterministic Candidate/Evidence identities remain in force; conflicts fail closed. No automated Review, Publish or Formal Release.

## Operational gates

1. Run the focused KP regression and CI on a new branch.
2. Review and merge. Rebuild/revalidate an **isolated** Storage Candidate from the merged source; never overlay changes into frozen package ZIP or live `:18770/:18772`.
3. Confirm existing historical FAILED Task still exists in the isolated Runtime DB, and confirm approved Provider config and contract probe.
4. Explicitly authorize a **new** generation/Provider-call budget and invoke it once.
5. After Extraction succeeds, perform Evidence/Evaluation, stop for **human review** and only then consider Publish/Release.
6. Run #473 EG01–EG07 only after #475 Formal NAND Knowledge Release is verified.

If any precondition is not provable, report BLOCKED rather than changing Source/Revision/topics, deleting Runtime records, using an invented Candidate, or bypassing human Review.
