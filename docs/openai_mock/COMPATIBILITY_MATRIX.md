# OpenAI Mock Compatibility Matrix V0.1

Baseline Date: 2026-09-20  
Status: ACCEPTANCE_PASS / V0.1 RC / READY_FOR_REVIEW  
Evidence: GitHub Actions Run #240 / 35516985444：

- OpenAI Mock V0.1 Python Acceptance：38 passed；
- 其中 Runtime → Mock real HTTP / Secret / Resume / Streaming / Storage E2E：15 cases；
- official OpenAI JS SDK：5/5 PASS；
- Runtime P0 + Storage historical regression：119 passed；
- 两个 CI job 均 PASS。

完整 AC-01 ～ AC-15 与 RT-MOCK-001 ～ RT-MOCK-016 映射见：

`docs/openai_mock/ACCEPTANCE_REPORT_V0.1.md`

Runtime-to-Mock 集成不是内存 Stub：测试使用现有 Runtime/Adapter/Provider Client 或官方 OpenAI SDK，通过真实本地 HTTP 调用 Mock，并用 Mock counter 对实际 HTTP 请求次数进行独立校验。

本轮验收还实际发现并修复了 Provider Boundary 对直接断连异常的分类缺口：`RemoteDisconnected` 所属 `ConnectionError` 现在进入 transport failure，由 Runtime Retry Policy / Retry Budget 管理。

## 兼容维护原则

1. Runtime 新增使用某个 OpenAI 官方 Endpoint 前，先更新本矩阵。
2. 每个新增 Endpoint 必须同时补 Contract Test。
3. Mock Control Plane 不得污染 `/v1` 请求 Schema。
4. SDK 升级后至少重新跑 Python + JS SDK Compatibility Gate。
5. Runtime Retry / Budget 等公共能力优先通过 Runtime → Mock real HTTP gate 验证，不只依赖内存 Stub。
6. 对未验证能力明确标记 NOT VERIFIED，不以“OpenAI-compatible”泛化替代测试证据。
