# PLATFORM TEST EXECUTION MANIFEST

TASK=MAJOR-REPEAT-MVP-PLATFORM-ADAPT-001  
PACKAGE=MAJOR_REPEAT_PRODUCT_MVP_TEST_RC2.zip  
BASE_RC1_SHA256=558780df0834899e027288155f0382d12933db2e6d563c2e6174b3b1c63c61ee

冻结入口：
- Linux: `./run_linux.sh --no-browser --port <port>`
- macOS: `./run_macos.sh --no-browser --port <port>`
- Windows: `run_windows.bat --no-browser --port <port>`

自动执行：
- Builder: `python scripts/build_major_repeat_test_rc2.py ...`
- Linux/macOS: `python scripts/posix_major_repeat_test_rc2.py --package <extracted>`
- Windows: `python scripts/windows_major_repeat_test_rc1.py --package <extracted>`

Evidence：
- security_scan.json
- linux_startup.log
- darwin_startup.log
- windows_startup.log
- CI step results
- RC2 zip + .sha256

判定：所有必测平台与 Regression PASS 才能给 PLATFORM_COMPATIBILITY_GATE=PASS。Target UAT 仍保持 PENDING，不能由本 Gate 改成 MVP_READY。