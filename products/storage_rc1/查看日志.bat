@echo off
setlocal
set "LOG=%~dp0RUN_LOG.txt"
if not exist "%LOG%" (
  >"%LOG%" echo STORAGE_PRODUCT_TEST_FULL_V1.12 has not been run yet.
  >>"%LOG%" echo Run run_windows.bat first.
)
start "" notepad.exe "%LOG%"
exit /b 0
