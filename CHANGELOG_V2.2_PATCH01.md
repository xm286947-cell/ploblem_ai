# REPEAT_CASE_ENGINE V2.2 Patch-01

## 目标

修复候选召回过严、部门参与重复判断、原因分类可信度不足，以及 AI JSON 输出偶发格式错误导致全量失败的问题。

## 主要变更

1. 部门默认模式由 `strict` 调整为 `preferred`。
2. M8.4 Repeat Decision 不再按部门删除候选。
3. Hard Filter 仅保留产品与领域边界；部门、原因分类退出硬过滤。
4. 原因分类权重降低，问题现象与根因描述权重提高。
5. 增加最小候选召回保护，阈值过滤后候选不足时自动补足 Top-N。
6. Query AI Prompt 增加原因分类校准规则：原始分类与根因冲突时以根因和失效机制为准。
7. Similarity 与 Repeat Prompt 明确：部门不参与判断，原因分类仅作为辅助证据。
8. 新增统一 AI JSON 解析与常见格式自动修复能力。
9. Query、Similarity、Repeat Decision 保存原始 AI 响应，并在解析失败时重试一次。
10. 单个候选 AI 失败继续保留原有容错，不阻断整个 Pipeline。

## 原始响应目录

```text
output/raw_ai/
  query_enrichment/
  similarity/
  repeat_decision/
```

## 验证

```text
83 passed
```
