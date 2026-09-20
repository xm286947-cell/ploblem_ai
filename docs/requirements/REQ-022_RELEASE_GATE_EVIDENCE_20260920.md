# REQ-022 Release Gate 自动回归证据（2026-09-20）

## 执行对象

- 代码基线：`repair/req022-acceptance@ff652bfb96519bf2ef652d5fa9c59f1726c30e18`
- 本地执行环境：macOS，Python 3.14.4，pytest 8.4.2
- 测试数据：仓库测试样本、合成数据和 Mock；未使用真实内部数据

## 结果

| Gate | 结果 | JUnit SHA-256 |
|---|---:|---|
| REQ-022 | 21 passed，1 warning | `f4e23a802d7b0f6b3d60427d4fcf776a48fe6ef9949bebbaca25434f47e3824b` |
| 旧 M7/M8 | 56 passed | `d22b1bac3ce575d5a7e9b85929b6b8fdf724bff7562a601fa8999506155ce227` |
| ITR/CS | 28 passed，1 warning | `dc6ff29fe2554e38471feb6f5b0198662337791da25de601910a2340fbd070bf` |
| 质量场景 | 103 passed，1 warning | `2a13ef6d243e962b712ae3822c114d6b6cb212748661dcd7d008555ec803bbba` |
| 问题工作台 | 31 passed，1 warning | `6ff840f24e0248eb21702ae43057adc00a3c2aea253e074dcebb4f074893210f` |
| 累计仓库回归（排除已知打包基线缺口） | 536 passed，1 warning | `214f86dd4c061b770eb00e17941e452dee83aa36f46f32a0dc8a6c229f117149` |

warning 均为 Starlette TestClient 使用 AnyIO 旧别名产生的弃用提示，不是功能失败。

## 全量首轮发现

未排除任何测试的首轮结果为 `536 passed, 1 failed, 1 warning`。唯一失败项为 `tests/test_upgrade_package_dependencies.py`：升级包脚本要求 Git 标签 `v1.1-p2-rc2-full-20260901`，但远端仓库没有该标签，导致 `git diff <tag>..HEAD` 无法执行。

该项记录为 `KNOWN EXCLUSION / NOT PASSED`。当前没有证据表明它是 REQ-022、旧 M7/M8、ITR/CS、质量场景或问题工作台的功能回归，但正式升级包 Gate 仍需补回标签或评审并变更包基线后复验。

## Release Gate 状态

- 自动代码回归：`PASS WITH KNOWN EXCLUSION`
- Windows 10 BAT：`UNVERIFIED`
- 真实外部模型：`UNVERIFIED`
- 人工脱敏业务验证：`UNVERIFIED`
- 正式发布结论：`NOT APPROVED`

本记录只证明上述代码基线在本地自动测试范围内的结果，不将 Mock、合成数据或 macOS 测试冒充环境验收。CI 在变更分支上复跑后，以 GitHub Actions Artifact 作为远端可下载证据。
