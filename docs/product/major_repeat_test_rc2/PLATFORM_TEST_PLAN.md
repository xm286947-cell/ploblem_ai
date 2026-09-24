# PLATFORM TEST PLAN

继承 `PRODUCT_TEST_EXECUTION_STANDARD_AND_BASELINE_V1.0`。本轮只验证交付包平台兼容性，不修改产品 Expected。

目标：
- 同一不可变 RC2 包在 Linux/macOS 可直接启动
- ZIP Entry 权限正确
- fresh extract 后权限正确
- 无手工 chmod
- Web / Repeat Risk / Case Library smoke 通过
- 优雅退出与 SQLite cleanup 通过
- Windows 既有入口不退化
- Secret / 个人路径扫描为 0

Gate 状态仅使用 PASS / FAIL / BLOCKED / NOT_TESTED。