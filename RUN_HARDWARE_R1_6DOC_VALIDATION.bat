@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set INPUT=data\r1_field_validation\input_word
set OUTPUT=data\r1_field_validation\output_snapshot
if not exist "%OUTPUT%" mkdir "%OUTPUT%"
set FOUND=0
for %%F in ("%INPUT%\*.docx") do (
  if exist "%%F" (
    set FOUND=1
    python scripts\hardware_r1_docx_snapshot.py "%%F" --output "%OUTPUT%\%%~nF.json"
    if errorlevel 1 exit /b !errorlevel!
  )
)
if "%FOUND%"=="0" (
  echo NO_DOCX_IN_%INPUT%
  exit /b 2
)
echo HARDWARE_R1_6DOC_FIELD_VALIDATION_PARSE=PASS
