@echo off
setlocal
cd /d "%~dp0"
if not exist outputs\case_knowledge_product_test mkdir outputs\case_knowledge_product_test
python -m pytest -q ^
  tests/product_test/test_case_knowledge_product_scenarios.py ^
  tests/test_kp_m01_source_document.py ^
  tests/test_kp_m02_candidate_evidence.py ^
  tests/test_kp_m03_ai_extraction.py ^
  tests/test_kp_d01_business_candidate_intake.py ^
  tests/test_kp_d02_evaluation.py ^
  tests/test_kp_d03_review_publish.py ^
  tests/test_kp_d04_release_query.py ^
  tests/test_kp_d05_processing_ui.py ^
  tests/test_kp_d06_storage_golden.py ^
  --junitxml=outputs\case_knowledge_product_test\junit.xml > outputs\case_knowledge_product_test\summary.txt 2>&1
set RC=%ERRORLEVEL%
type outputs\case_knowledge_product_test\summary.txt
exit /b %RC%
