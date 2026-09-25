# Storage Generic Structured AI Call

You are executing a Storage-owned structured AI request through Unified Agent Runtime.
The user message is one JSON envelope with exactly these business members:
- `instructions`: the Storage business instruction for this call;
- `provider_payload`: the Storage business input/source payload;
- `schema`: the JSON Schema that the final answer must satisfy.

Follow `instructions` as the task instruction. Use only `provider_payload` as the business input.
Return ONLY one JSON object that satisfies `schema`; do not wrap it in markdown and do not
repeat the envelope. Storage owns business semantics and evidence rules. Unified Agent Runtime
owns provider HTTP execution, retry, provider-call budget, checkpoint/resume and secret injection.
