@echo off
setlocal
cd /d "%~dp0..\app"
"..\.venv\Scripts\python.exe" -m pytest -q tests\test_golden_e2e_001.py tests\test_repeat_web_mvp.py tests\test_quality_capability_p0_workbench_ued.py tests\test_repeat_itr_subject.py tests\test_repeat_search_contract.py tests\test_repeat_result_contract.py tests\test_historical_case_consumer_contract.py tests\test_case_publish_adapter.py tests\test_case_publish_service.py
endlocal & exit /b %errorlevel%
