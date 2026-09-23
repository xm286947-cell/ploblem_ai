# QUALITY CAPABILITY V1.1 P2 RC1 BASELINE

冻结日期：2026-08-30
状态：VERIFIED BASELINE

## 唯一代码根

本文件所在目录是后续开发、测试和打包的唯一代码根。上层 `p2_baseline/`、`deliverables/` 和本目录中的 `releases/` 仅作为历史交付参考，不再作为开发源。

## 固定启动入口

Windows 用户继续双击：

```text
start_quality_capability_p1.bat
```

默认访问：

```text
http://127.0.0.1:8080/issues
```

默认运行数据库：

```text
knowledge/quality_issue_v1.db
```

## 已冻结业务链路

1. 产品配置与动态业务。
2. Excel 数据接入、表头诊断、Preview-before-Commit。
3. Mapping Draft、Validate、Activate、字段差异处理。
4. 问题工作台、月份筛选、归属年月修订、上一条/下一条。
5. 单问题及批量 AI 分析、多模型动态分配、运行记录。
6. 原始数据、标准化数据、AI 结果和人工分析。
7. 发生/流出 MRC、再发风险、能力缺口和硬件关联。
8. 质量洞察及精确下钻。
9. 产品质量综合报告、分维度生成、发布、删除草稿和 PDF 导出。
10. 正向风险评估基础入口。

## 数据与配置边界

纳入基线：

- Python 源码、HTML/CSS/JS。
- Prompt 与 YAML 配置模板。
- BAT 启动入口。
- 数据库 Schema/自动升级代码。
- 根目录 `tests/` 回归测试。

不纳入基线：

- `knowledge/*.db`、SQLite 临时文件。
- `knowledge/raw_*` 等本地业务/测试运行数据。
- `output/` 日志、诊断信息和 AI 生成结果。
- `.pytest_cache/`、`__pycache__/`。
- `releases/` 历史补丁和历史完整包。
- API Key、Token 等密钥；配置只允许引用环境变量。

## 数据保护

- 基线包不包含用户数据库，不覆盖已存在的 `knowledge/quality_issue_v1.db`。
- 数据库升级由 Repository 初始化时执行增量 `ALTER/CREATE`。
- 归属年月修改保留审计记录，不创建新问题版本，不使既有 AI 分析失效。
- 安装新基线前应复制备份整个 `knowledge/` 目录。

## 验证结论

```text
pytest -q tests
397 passed
0 failed
```

完整仓库直接执行 `pytest` 会误收集 `releases/` 内历史补丁测试副本，正式命令固定为 `pytest -q tests`。

## 后续变更规则

1. 一个需求一个提交，禁止把运行数据混入提交。
2. 修改稳定链路必须补专项测试，并运行 `pytest -q tests`。
3. 发布包必须由 `scripts/build_baseline_package.py` 生成。
4. Patch 只包含相对本基线发生变化的文件。
5. 每次发布记录版本、数据库变更、接口变更、测试结果和兼容性结论。
