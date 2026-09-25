# Storage MVP RC1 人工 Golden Acceptance

必须在目标 Windows 环境执行，不能用 sample/mock 替代。

1. 核对内置正式 Knowledge Release：`knowledge_release/current/release_manifest.json` 的版本应为 `KP-STORAGE-RC1-VALIDATION-001`，状态接口应返回 READY。
2. 配置真实 Provider endpoint 与 Secret 环境变量。
3. `run_windows.bat`：确认 P01-P08 可访问。
4. 导入一份真实 Storage 官方规格资料，完成 Document Analysis → Coverage → Candidate/Evidence → Human Confirm → Reviewed Specification。
5. P05：选两个器件，验证“全部/仅差异/仅缺失”，至少包含一个 NOT_CHECKED 或 NOT_FOUND。
6. P06：验证指标→工程含义→数据源→读取方法→判读→Evidence；Runtime Observation 如有值必须带真实来源/采集时间。
7. P07：验证 Fact Diff → 技术含义 → 软件/测试/监控影响 → 待验证项；系统不得自动给出替代结论。
8. P08：普通用户与维护工作区分离；Knowledge Publish 只能由 Knowledge Production Gate 完成。
9. 正式 Knowledge 查询返回 Published Knowledge Object + Evidence + Source Reference + knowledge_release_version。
10. 执行 `selftest_windows.bat`，保存 RUN_LOG、provider trace 和 Evidence 截图/记录。

通过条件：`REAL_PROVIDER_GATE=PASS`、`REAL_EMMC_GATE=PASS`、`KNOWLEDGE_RELEASE_CONSUMPTION=PASS`、`GOLDEN_PATH=PASS`、`PRODUCT_GATE=PASS`。
