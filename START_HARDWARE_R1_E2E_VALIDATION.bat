@echo off
setlocal
cd /d "%~dp0"
if defined PYTHON (
  "%PYTHON%" scripts\hardware_r1_e2e_validation_start.py %*
) else (
  py -3 scripts\hardware_r1_e2e_validation_start.py %*
)
endlocal
