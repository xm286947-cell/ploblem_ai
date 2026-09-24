# PLATFORM COVERAGE MATRIX

| Product/Delivery Concern | Test Case | Fixture | Expected | Evidence | Gate |
|---|---|---|---|---|---|
| POSIX package permission | PLAT-01/02/07 | RC2 ZIP | 0755 after native unzip | zip mode + stat | PACKAGE |
| Linux launcher | PLAT-03~06 | Synthetic engineering fixture | direct start + smoke + cleanup | linux_startup.log | LINUX |
| macOS launcher | PLAT-08~10 | Synthetic engineering fixture | direct start + smoke + cleanup | darwin_startup.log | MACOS |
| Windows compatibility | PLAT-11 | Synthetic engineering fixture | existing batch PASS | windows_startup.log | WINDOWS |
| Security | PLAT-12 | Full staged package | findings=0 | security_scan.json | SECURITY |
| Product semantics | inherited RC1 regression | Frozen RC1 tests | failure delta=0 | pytest logs | REGRESSION |

平台 Fixture 不替代正式 Target UAT 样本。