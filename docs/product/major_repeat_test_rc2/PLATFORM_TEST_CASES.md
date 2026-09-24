# PLATFORM TEST CASES

| ID | 场景 | Expected |
|---|---|---|
| PLAT-01 | ZIP launcher entry | run_linux.sh / run_macos.sh / run_posix.sh = 0755 |
| PLAT-02 | Linux fresh extract | launcher 保持 executable |
| PLAT-03 | Linux direct start | 不 chmod，直接 ./run_linux.sh 成功 |
| PLAT-04 | Linux Web smoke | initialization / ITR / cases = 200 |
| PLAT-05 | Linux Repeat smoke | SUCCESS，Why Relevant + Evidence 存在 |
| PLAT-06 | Linux shutdown | SIGINT 正常退出，SQLite 可 rename |
| PLAT-07 | macOS fresh extract | launcher 保持 executable |
| PLAT-08 | macOS direct start | 不 chmod，直接 ./run_macos.sh 成功 |
| PLAT-09 | macOS Web/Repeat/Case | 全部 PASS |
| PLAT-10 | macOS shutdown | SQLite cleanup PASS |
| PLAT-11 | Windows regression | 原 run_windows.bat 启动/退出 PASS |
| PLAT-12 | Security | Secret/个人路径/临时 DB 不入包 |
