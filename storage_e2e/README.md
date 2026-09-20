# Storage × Unified Agent Runtime E2E-01 — JSON 截断验证包 V0.1

基线：main@5f9d0a7093ef1ea2531767d84c05b234eb95335a

本包验证：
- finish_reason=length -> Runtime VALIDATION retry
- Runtime 是唯一 Retry Owner
- provider_calls / Retry Budget 可审计
- 半截 JSON 不 Commit
- 完整 JSON 通过 Storage Schema 后 Atomic Commit
- 最终进入字段级 Golden Diff

执行：
python -m pytest tests/test_agent_runtime_p0_storage_e2e_json_truncation.py -q

V0.1 使用确定性 Provider Stub 稳定复现截断。
下一步 V0.2 替换为真实 Provider：正常预算一次 + 压低 max_tokens 强制截断一次。
