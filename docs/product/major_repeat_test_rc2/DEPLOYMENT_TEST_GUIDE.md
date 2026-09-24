# MAJOR_REPEAT_PRODUCT_MVP_TEST_RC2 部署测试说明

RC2 仅增加 Linux/macOS 启动与平台 Gate，不改变 Repeat、Historical Case、Runtime、Knowledge Platform 业务语义。Target UAT 样本授权仍独立处理，当前 `TARGET_ENV=PENDING`、`MVP_READY=NO`。

## Linux
解压后直接执行：
```bash
./run_linux.sh --no-browser --port 8080
```
禁止测试人员手工 `chmod`。若无法直接执行，判 `FRESH_EXTRACT_PERMISSION=FAIL`。

## macOS
解压后直接执行：
```bash
./run_macos.sh --no-browser --port 8080
```
同样禁止手工修权限。首次运行创建包内 `.venv` 并安装 `app/requirements.txt`。

## Windows
继续使用：
```text
run_windows.bat --no-browser --port 8080
```
Windows 仅做 Regression，不重新定义产品入口。

三个平台都复用现有 `create_p0_app` Web、同一 `scripts/major_repeat_test_rc1.py` 产品启动逻辑。Synthetic smoke 只用于工程平台 Gate，不等价于 Target UAT。

退出使用 Ctrl+C / SIGINT；必须验证 SQLite 可重命名，证明句柄已释放。