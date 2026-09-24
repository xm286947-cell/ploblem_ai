@echo off
setlocal
python -m pytest -q tests\test_storage_rc1_mock_assets.py tests\test_openai_mock_storage_m01_m08.py
exit /b %ERRORLEVEL%
