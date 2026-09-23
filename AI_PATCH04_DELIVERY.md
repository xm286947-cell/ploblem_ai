# AI PATCH04 DELIVERY

Version: KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_ENGINE_V1.0_ADDENDUM01_A7_RC1_AI_PATCH04
Status: Token Budget & Structured Output Stability

## Changes
- Stage max_tokens: occurrence 2048, escape 2048, recurrence 2048, capability_gap 4096.
- Capability Gap limited to TOP 3 per dimension (Technical / Management / Governance).
- Capability Gap output is post-limited by Engine as a second guardrail.
- Recurrence / Capability Gap prompts require concise JSON and avoid repeated evidence text.
- Analysis debug records input_chars, output_chars, max_tokens, finish_reason, usage, response_truncated.
- OpenAI-compatible finish_reason=length is treated as explicit token truncation and retried.
- PATCH03 behavior remains: completed stages are reused by default; force re-analysis is supported.
- PATCH03 gap UUID / stage timeout / stale RUNNING fixes remain.

## Tests
PATCH03 + PATCH04 focused: 7 passed
Full regression: 196 passed / 0 failed
