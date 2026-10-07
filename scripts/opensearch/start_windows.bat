@echo off
setlocal

if "%OPENSEARCH_HOME%"=="" (
  echo SEARCH_ENGINE_HOME_REQUIRED: set OPENSEARCH_HOME to the extracted OpenSearch directory.
  exit /b 2
)

echo(%OPENSEARCH_HOME%| find " " >nul
if not errorlevel 1 (
  echo SEARCH_ENGINE_PATH_HAS_SPACES: OpenSearch Windows extraction path must not contain spaces.
  exit /b 3
)

if "%HARDWARE_SEARCH_PORT%"=="" set "HARDWARE_SEARCH_PORT=9200"
if "%HARDWARE_SEARCH_DATA_DIR%"=="" set "HARDWARE_SEARCH_DATA_DIR=%USERPROFILE%\.hardware-knowledge\search-w0"
if "%OPENSEARCH_JAVA_OPTS%"=="" set "OPENSEARCH_JAVA_OPTS=-Xms512m -Xmx512m"

if not exist "%HARDWARE_SEARCH_DATA_DIR%\data" mkdir "%HARDWARE_SEARCH_DATA_DIR%\data"
if not exist "%HARDWARE_SEARCH_DATA_DIR%\logs" mkdir "%HARDWARE_SEARCH_DATA_DIR%\logs"

if not exist "%OPENSEARCH_HOME%\bin\opensearch.bat" (
  echo SEARCH_ENGINE_BINARY_NOT_FOUND: %OPENSEARCH_HOME%\bin\opensearch.bat
  exit /b 4
)

echo Starting OpenSearch W0 on http://127.0.0.1:%HARDWARE_SEARCH_PORT%
echo Data: %HARDWARE_SEARCH_DATA_DIR%\data
echo Logs: %HARDWARE_SEARCH_DATA_DIR%\logs

call "%OPENSEARCH_HOME%\bin\opensearch.bat" ^
  -Ecluster.name=hardware-search-w0 ^
  -Enode.name=hardware-search-w0 ^
  -Ediscovery.type=single-node ^
  -Enetwork.host=127.0.0.1 ^
  -Ehttp.port=%HARDWARE_SEARCH_PORT% ^
  -Eplugins.security.disabled=true ^
  -Epath.data="%HARDWARE_SEARCH_DATA_DIR%\data" ^
  -Epath.logs="%HARDWARE_SEARCH_DATA_DIR%\logs"

exit /b %ERRORLEVEL%
