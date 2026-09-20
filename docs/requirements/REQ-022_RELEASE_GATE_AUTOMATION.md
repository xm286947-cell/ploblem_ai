# REQ-022 Release Gate 自动回归基线

基线：`repair/req022-acceptance@ff652bfb96519bf2ef652d5fa9c59f1726c30e18`

## 自动 Gate 范围

| Gate | 覆盖内容 | 证据文件 |
|---|---|---|
| REQ-022 | 重大问题独立库、长文续跑、证据继承、模型执行状态、相似检索、缓存失效、多 ITR/Event 隔离、备份恢复、Web 与交付包 | `req022.xml` |
| 旧 M7/M8 | 查询解析、归一、AI 增强、检索 Profile、相似检索、相似/方案/重复分析与交付 | `legacy-m7-m8.xml` |
| ITR/CS | 导入、版本、分组隔离、关联、去重、错误数据清理、CS 优先及 ITR 补充 | `itr-cs.xml` |
| 质量场景 | 单问题逆向分析、候选场景、词典、证据资产、决策摘要、画像解释、多来源去重与运营范围 | `quality-scenarios.xml` |
| 问题工作台 | 问题详情、月份导航、批量分析、工作台布局、原始字段展示与洞察下钻 | `issue-workbench.xml` |
| 累计仓库回归 | 除下述已知打包基线缺口外的全部自动测试 | `cumulative.xml` |

CI 工作流为 `.github/workflows/req022-release-gate.yml`。每一类生成独立 JUnit XML，并统一上传为保留 90 天的 `req022-release-gate-evidence-<commit>` Artifact。

## 已知排除项

`tests/test_upgrade_package_dependencies.py` 依赖 Git 基线标签 `v1.1-p2-rc2-full-20260901`。该标签目前不存在于远端仓库，因此全量首轮会在读取版本差异时失败。Release Gate 的累计仓库回归暂时只排除这一项，并在 Artifact 中写入 `KNOWN_EXCLUSIONS.txt`。

该排除项不得记为通过。恢复缺失标签，或正式变更升级包基线并独立评审后，才可移除排除。

## 不属于自动 PASS 的验证

以下项目保持 `UNVERIFIED`，自动测试结果不得替代：

- Windows 10 BAT 启动与完整操作链路；
- 真实外部模型的网络、密钥、供应商响应及兼容性；
- 使用人工脱敏业务数据进行的业务验收。

Mock、合成数据、macOS 或 Linux CI 的成功均不得将上述状态改为 PASS。
