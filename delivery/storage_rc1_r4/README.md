# Storage RC1 R4 Delivery Quality Source

Task: `STORAGE-DELIVERY-QUALITY-HARDENING-001`.

This directory is the Git source of truth for the R4 delivery layer. The previous R3 ZIP is a frozen, SHA-pinned input only; it is not treated as source code.

Official launch contract:
- Linux/macOS: `bash start_test.sh mock` / `bash start_test.sh real`
- Windows: `run_windows.bat`

Executable-bit rule:
- every `.sh` ZIP entry must carry Unix regular-file mode `0755`;
- runtime behavior MUST NOT depend on an extractor restoring that mode;
- shell-to-shell calls use explicit `bash`;
- no `chmod +x` repair is allowed in release or test flows.

R4 build provenance is mandatory: `SOURCE_BRANCH`, 40-hex `SOURCE_COMMIT`, and `BUILD_SCRIPT_VERSION` are written into `RELEASE_MANIFEST.json`.

The package contract gate validates CRC, duplicate/illegal paths, UTF-8 Chinese entry flags, the frozen 254-path SHA manifest, required launcher/config files, ZIP shell metadata, provenance, and implicit `./helper.sh` dependencies. Linux release validation additionally rebuilds evidence from both Python `zipfile` and system `unzip` fresh extracts and launches the official Bash entry without permission repair.
