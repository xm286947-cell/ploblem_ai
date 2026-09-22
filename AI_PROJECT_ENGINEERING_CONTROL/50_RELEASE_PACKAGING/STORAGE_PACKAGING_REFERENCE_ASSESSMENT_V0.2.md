# Storage Packaging Reference Implementation 接管评估 V0.2

## 新发现：真实 Build Entry 已定位

Storage 后续 UKCI-07 已经把正规打包能力固化成真实代码：

`scripts/ukci07_delivery.py::build_package`

CLI：
`python -m scripts.ukci07_delivery --build --source <root> --output <dist> --expected-commit <sha>`

构建行为已明确：
1. 从 source 建临时 staging；
2. COPY_ALL_EXCEPT_EXCLUSIONS；
3. 排除 .git/.venv/cache/data/logs/旧二进制交付件/patch；
4. 写 release manifest / release notes / known baseline failures；
5. 校验 required files；
6. 扫开发机绝对路径；
7. 扫 Secret；
8. 校验 build commit 与 contract preservation；
9. Gate 通过后生成 ZIP；
10. 输出 ZIP SHA256。

这已经不是“只有一个正规包”，而是**存在可抽象的真实 Packaging Builder**。

## 公共化判断

可直接提炼：
- clean staging
- include/exclude
- required files gate
- secret scan
- absolute-path scan
- manifest generation
- provenance/build commit gate
- pre-zip validation
- ZIP + SHA256

继续留 Storage Profile：
- UKCI contracts
- Storage required files
- Golden tests
- startup/acceptance entry
- project-specific excludes
- known Storage baseline failures

## 关键安全改进

UKCI-07 新 builder 已明确排除：
- data
- logs
- .venv
- .git
- RUN_LOG.txt
- release/storage_app.log
- zip/bundle/patch

因此上一轮对 V1.12 包中 SQLite/log/config 的疑问已有部分演进答案：**新的正规 builder 已开始把运行数据与日志从交付包中剥离。**

配置仍只允许模板/无 Secret 配置；公共层必须保持 Secret scan。

## 当前状态

Build entry：CONFIRMED  
Include/exclude source：CONFIRMED  
Public Profile：DRAFTED  
Release Manifest Contract：DRAFTED  
公共 Packaging Core 编码：NOT_STARTED

下一步：
用 Storage UKCI builder 作为语义基准，设计最小 Packaging Core Adapter；第一阶段不改变 Storage 现有 builder 行为。
