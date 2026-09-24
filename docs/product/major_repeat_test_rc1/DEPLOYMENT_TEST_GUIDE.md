# 提测部署说明

本包是 `TEST_RC`，状态为 `MVP_INTEGRATION`。`TARGET_ENV=PENDING`，`MVP_READY=NO`。

1. 在 Windows 10/11 的新目录解压 ZIP，保留顶层 `MAJOR_REPEAT_PRODUCT_MVP_TEST_RC1` 目录。安装 Python 3（建议 3.11）并确保可访问 Python 包源。首次运行会在包内创建 `.venv` 并安装 `app/requirements.txt`。
2. 双击 `run_windows.bat`。脚本先用临时合成数据执行 smoke，再初始化 `data/runtime/quality_capability_p0.sqlite3`、建立合成历史案例检索索引并启动仓库现有 `create_p0_app` Web。浏览器打开 `http://127.0.0.1:8080/p0/issues/K-ITR-1`。
3. 退出时在控制台按 `Ctrl+C`，确认正常结束、无 SQLite 文件占用或 WinError 32。重新启动应复用已有 P0 数据库；合成数据不会因重启重复创建。
4. 真正的目标环境 UAT 使用获准的 ITR 和 Historical Case。由数据 Owner 在目标环境准备 P0 数据库、Historical Case 发布资产及检索索引后，运行 `run_windows.bat --target --db <目标环境数据库路径>`。`--target` 不会注入合成样本。请勿将真实数据或密钥回写本 ZIP 或源仓库。

合成演示的 Repeat 检索使用仓库现有 `local_hash` Embedding，不需要外部 Provider。如果目标环境还要执行 AI 分析，则按目标环境授权流程设置 `QUALITY_AI_BASE_URL`、`QUALITY_AI_API_KEY` 等环境变量，并检查 `app/config/runtime/model.yaml`。包内 `config/model.local.example.yaml` 仅是占位示例。不要把真实 Secret 写入提测包。

`scripts/smoke_test.bat` 可单独做离线合成链路检查；`scripts/golden_test.bat` 运行工程 Golden 和相关回归。P0 初始化由现有 `P0Initializer` 执行，包中保留其 Schema 和配置，不另造 Migration。`SHA256SUMS`、`manifest.json` 和 ZIP 同名 `.sha256` 用于完整性核验。

本包尚未在 Windows 实机执行启动 Gate，状态为 `TARGET_WINDOWS_PENDING`。测试人员须记录系统、Python 版本、启动日志、退出结果及 WinError 32 观察结果。
