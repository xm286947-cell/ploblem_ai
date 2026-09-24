# PLATFORM RUNBOOK

执行顺序固定：
1. 核 RC2 ZIP SHA256。
2. 检查 ZIP Entry mode。
3. 使用平台原生 `unzip` fresh extract。
4. 禁止 chmod，检查 `test -x run_<platform>.sh`。
5. 直接执行 launcher。
6. 等待 `/api/v2/initialization/status`。
7. 打开 ITR Workbench 与 Case Library。
8. 执行 Synthetic Repeat Query，检查 Why Relevant / Evidence。
9. SIGINT 优雅退出。
10. rename SQLite 检查句柄释放。
11. 保存平台日志。
12. Windows 用原 batch 做 regression。

失败后只收 Evidence，不修改 Expected，不现场修包。若需要修改包内容，必须新 package + 新 SHA。