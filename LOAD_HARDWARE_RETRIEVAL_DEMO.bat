@echo off
setlocal
cd /d "%~dp0"
if "%PYTHON_BIN%"=="" set "PYTHON_BIN=python"
"%PYTHON_BIN%" tools\hardware_retrieval_demo_seed.py --execute-demo
echo.
echo Demo data loaded. Open:
echo http://127.0.0.1:8080/p0/hardware-cases/search?q=有哪些mcu的问题
pause
