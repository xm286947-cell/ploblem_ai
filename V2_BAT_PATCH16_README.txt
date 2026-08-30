V2 BAT startup package - PATCH17

Install into the V2 full-package root that contains start_quality_capability_p1.bat.

1. Stop the running service.
2. Extract all files into the V2 root and overwrite files with the same names.
3. Double-click start_quality_capability_p1.bat.
4. Do not run init, database migration, or a Python command.

Pages:
- http://127.0.0.1:8080/issues
- http://127.0.0.1:8080/statistics
- http://127.0.0.1:8080/product-reports

Large product reports automatically use 25-issue batches and a final reduce step. Repeated unchanged batches are reused from cache.

Product report prompt location:
quality_knowledge\prompts\product_quality_report.md

legacy_service.py is only an internal compatibility-adapter filename. This package uses the V2 BAT startup path.
