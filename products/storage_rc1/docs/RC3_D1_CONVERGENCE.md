# RC3-D1 四器件抽取链路收敛

状态：开发实现完成 / 本地回归通过
日期：2026-09-19

## 范围

只收敛 RC3 正式规格事实抽取主链，不另建第二套 Production 系统。现有 PDF→Markdown、SQLite、人工核对、Reviewed Specification、Device Conclusion、产品族/料号展示和知识 API 保持复用。

## 新主链

Structured Markdown / Source Bundle
→ `ai.extract_specification_bundle_once` / `extract_specification_once`
→ `_adapt_single_pass` Contract Adapter
→ `_resolve_locator` Evidence Resolver
→ `_normalize_candidate_representation` + schema/status validation
→ deterministic Review Queue
→ `core.import_document` persistence
→ existing Reviewed Specification / Device Conclusion

## 关键实现

- 正常事实抽取：1 次模型调用。
- Contract Adapter 支持 `field_key` 与 legacy `name` 兼容，不修改事实值来匹配 Golden。
- Evidence locator 通过 `source_id + page + quote anchor` 在本地解析；未解析证据进入 Review Queue。
- Source Bundle 支持多个冻结来源；conflict 可携带两侧 `conflict_evidence`，保留 source provenance。
- evidence provenance 独立保存到 `candidate_evidence_provenance`，不破坏旧 `candidate_evidence` 表的 8 列兼容契约。
- 新增 `extraction_runs` 保存 schema_valid、model_calls、unresolved_evidence、review_required、review_queue。
- 新增 `GET /api/devices/{id}/extraction-status`。
- `POST /api/devices/{id}/final-review` 对新链路仅在 Review Gate 命中时允许调用；旧数据没有 extraction_run 时保持兼容。
- Web 导入完成后不再自动调用 Final Review。
- 默认 Qwen profile：`qwen3.8-max`，Token Plan OpenAI-compatible endpoint，`enable_thinking=false`。

## 本地验收

`python3 -m pytest -q`：68 passed。

新增覆盖：
- single-pass 只调用模型一次；
- source-native evidence resolver；
- Contract Adapter legacy name→field_key；
- multi-source conflict 双 provenance；
- 非关键 missing 不触发 Review；
- Review Gate 未命中时阻止 Final Review 模型调用。

## 未完成

- 真实 NAND/NOR/eMMC/SSD 四份 Golden PDF 的在线 qwen3.8-max E2E 尚未在本环境执行。
- 当前 RC3 Web 导入仍是单 PDF 入口；多 Source Bundle 已具备抽取契约，但补充官方网页 snapshot 的产品化导入/持久化入口尚未接 UI。
- Codex 独立测试尚未在本环境执行（当前运行环境未发现 Codex CLI）。
