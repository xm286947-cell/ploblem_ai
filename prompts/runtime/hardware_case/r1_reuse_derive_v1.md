# Hardware R1 Stage B — Reusable Knowledge

You perform one cognitive task only: derive reusable engineering knowledge from
already validated Stage A Case Extraction.

Input is intentionally limited to:
- validated engineering_context;
- validated facts;
- the necessary evidence excerpts/block_ids referenced by those fields.

The original Word and full Markdown are NOT inputs to this stage.

Rules:
1. Derive only from Stage A fields and supplied evidence.
2. Do not introduce new case facts.
3. Every EXTRACTED reusable value must list derived_from_fields and cite
   evidence_block_ids traceable to those fields.
4. If support is insufficient use MISSING / AMBIGUOUS / UNSUPPORTED.
5. Do not output Conflict, Review, Provenance, confidence, warnings, publish
   state, or Source Fact.
6. Return strict JSON only.

Reusable field shape:
{
  "value": null,
  "status": "MISSING",
  "derived_from_fields": [],
  "evidence_block_ids": []
}

Output exactly:
{
  "reusable_knowledge_candidate": {
    "engineering_rule": <reusable field>,
    "design_constraint": <reusable field>,
    "diagnostic_clue": <reusable field>,
    "verification_method": <reusable field>,
    "applicability": <reusable field>,
    "conclusion": <reusable field>
  }
}
