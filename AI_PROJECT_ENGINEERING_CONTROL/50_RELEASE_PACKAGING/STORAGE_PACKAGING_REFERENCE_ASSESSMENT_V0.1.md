# Storage Packaging Reference Implementation 接管评估 V0.1

日期：2026-09-22  
Owner：CMO / Engineering Control  
状态：REFERENCE_CANDIDATE_CONFIRMED / SOURCE_BUILD_ENTRY_PENDING

## 1. 结论

Storage 已具备足够成熟的“产品交付包结构与发布证据链”，适合作为统一 Packaging 的 Reference Implementation。

本阶段不新造 Packaging Engine，也不复制 Storage 脚本。先把 Storage 已验证能力拆为 COMMON / STORAGE_SPECIFIC / NEEDS_REVIEW，再做公共化。

## 2. 已核验真实资产

主参考包：
- STORAGE_PRODUCT_TEST_FULL_V1.12.zip
- 包内 PACKAGE_MANIFEST.md / PATCH_MANIFEST.md
- VERSION
- SHA256SUMS.txt / 多阶段 SHA256SUMS
- release/BUILD_INFO.json
- release/BUILD_VALIDATION.txt
- release/BASELINE.md
- release/VALIDATION.md
- release/CHANGESET.md
- release/KNOWN_GAPS.md
- run_windows.bat
- scripts/preflight.py
- selftest_windows.bat

后续 Storage 交付继续沿用“交付说明 + 测试结果 + SHA256 + Windows validation ZIP”的模式。

## 3. 能力分类

### COMMON — 建议提升为公共 Packaging 能力

1. Package identity
   - package name
   - product version
   - build date
   - status

2. Provenance
   - source baseline
   - dependency/runtime baseline
   - snapshot source commit
   - workflow/digest evidence
   - package-local provenance gate

3. Manifest
   - PACKAGE_MANIFEST
   - PATCH_MANIFEST / changeset
   - BUILD_INFO
   - BASELINE
   - VALIDATION
   - KNOWN_GAPS

4. Integrity
   - SHA256SUMS
   - package digest / dependency artifact digest

5. Validation evidence
   - regression result
   - focused tests
   - Mock/E2E status
   - Real Provider / field validation explicit NOT_RUN/PASS
   - inherited known failures not hidden

6. Delivery modes
   - FULL package
   - integrated hotfix/full package
   - validation package
   - patch/incremental package

7. Runtime startup safety
   - package-local runtime/dependencies
   - preflight before execution
   - source/provenance checks
   - normal startup and self-test separation

8. Release trace
   - package must be linkable to CMO Trace / Verification / Baseline

### STORAGE_SPECIFIC — 保留在 Storage Package Profile

- storage_life/**
- Storage Agent / Prompt / Schema
- Storage model profile names
- Storage Web port
- Storage Real Agent endpoint defaults
- Storage-specific synthetic PDFs / Golden cases
- Storage-specific product E2E
- Storage UI / RUN_LOG vocabulary
- Storage domain database initialization/data policy

### NEEDS_REVIEW — 公共化前必须确认

1. 真正“生成 V1.12 ZIP”的 build entry/script 未在当前包内直接识别。
2. include/exclude 的生成源规则未形成单一可读 Package Profile。
3. V1.12 包内仍包含 SQLite 文件；统一规则必须区分“空初始化库/测试库/真实业务库”，不能直接照搬。
4. config/model.local.yaml 被打入包；统一规则必须确认其是否仅为无 Secret 的部署配置，真实 Secret 必须排除。
5. release/storage_app.log 等运行日志存在于包内；公共规则应默认不带运行日志，只允许明确的空/示例或验收证据。
6. Storage mother baseline 与 Runtime baseline 来源不同；公共 Manifest 需要统一 source_components[] 表达多组件来源。
7. FULL / PATCH / VALIDATION 三种包的统一命名与 profile schema 尚未冻结。

## 4. 建议公共目标

统一能力不叫“Storage Packaging”，目标为：

Packaging Core
  + Package Profile
  + Release Manifest
  + Integrity Gate
  + CMO Trace Link

Storage 作为 Reference Consumer #1。

## 5. 最小公共 Manifest

至少包含：
- package_id
- package_type
- product/project_id
- version
- build_time
- source_components[]
  - repository
  - branch/tag
  - commit
  - artifact_digest
- trace_ids[]
- change_ids[]
- verification_ids[]
- baseline_ids[]
- include_profile
- exclude_profile
- validation_summary
- known_gaps
- files_manifest
- package_sha256

## 6. 下一步

P1：找到 Storage 当前真实 build entry / 生成脚本与 include/exclude 源规则。  
P2：基于 Storage 事实抽取 PACKAGE_PROFILE_V0.1 + RELEASE_MANIFEST_V0.1。  
P3：Storage 原包用公共 Profile 重建一次，做 byte/content/behavior 对照。  
P4：通过后再接第二个 consumer，不先迁所有项目。

熔断：
- 找不到真实 build source 时，不反推重写打包器；
- 发现 Secret/真实业务 DB/客户数据进入现有包时，立即停止公共化并先修安全 Gate；
- 公共化要求改变 Storage 运行语义时，停止并升级架构评审。
