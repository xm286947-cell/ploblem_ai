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
if "%OPENSEARCH_JAVA_HOME%"=="" set "OPENSEARCH_JAVA_HOME=%OPENSEARCH_HOME%\jdk"
if not exist "%OPENSEARCH_JAVA_HOME%\bin\java.exe" (
  echo SEARCH_JAVA_HOME_INVALID: %OPENSEARCH_JAVA_HOME%
  exit /b 5
)
if "%OPENSEARCH_JAVA_OPTS%"=="" set "OPENSEARCH_JAVA_OPTS=-Xms512m -Xmx512m"

if not exist "%HARDWARE_SEARCH_DATA_DIR%\data" mkdir "%HARDWARE_SEARCH_DATA_DIR%\data"
if not exist "%HARDWARE_SEARCH_DATA_DIR%\logs" mkdir "%HARDWARE_SEARCH_DATA_DIR%\logs"
rem The bundled jvm.options writes GC logs to logs\gc.log relative to OPENSEARCH_HOME.
rem The Windows ZIP can omit that directory, so create it explicitly.
if not exist "%OPENSEARCH_HOME%\logs" mkdir "%OPENSEARCH_HOME%\logs"

if not exist "%OPENSEARCH_HOME%\bin\opensearch.bat" (
  echo SEARCH_ENGINE_BINARY_NOT_FOUND: %OPENSEARCH_HOME%\bin\opensearch.bat
  exit /b 4
)

rem W0 follows the official Windows guidance: disable Security and configure
rem single-node networking in opensearch.yml rather than relying on -E flags.
rem Use a disposable config copy so the extracted distribution remains pristine.
set "W0_CONFIG_DIR=%HARDWARE_SEARCH_DATA_DIR%\config-w0"
if exist "%W0_CONFIG_DIR%" rmdir /s /q "%W0_CONFIG_DIR%"
mkdir "%W0_CONFIG_DIR%"
xcopy "%OPENSEARCH_HOME%\config\*" "%W0_CONFIG_DIR%\" /E /I /Y >nul
if errorlevel 1 (
  echo SEARCH_CONFIG_COPY_FAILED
  exit /b 6
)

set "YAML_DATA_DIR=%HARDWARE_SEARCH_DATA_DIR:\=/%"
>> "%W0_CONFIG_DIR%\opensearch.yml" echo.
>> "%W0_CONFIG_DIR%\opensearch.yml" echo # Hardware Knowledge W0 local-only overrides
>> "%W0_CONFIG_DIR%\opensearch.yml" echo discovery.type: single-node
>> "%W0_CONFIG_DIR%\opensearch.yml" echo network.host: 127.0.0.1
>> "%W0_CONFIG_DIR%\opensearch.yml" echo http.port: %HARDWARE_SEARCH_PORT%
>> "%W0_CONFIG_DIR%\opensearch.yml" echo plugins.security.disabled: true
>> "%W0_CONFIG_DIR%\opensearch.yml" echo path.data: %YAML_DATA_DIR%/data
>> "%W0_CONFIG_DIR%\opensearch.yml" echo path.logs: %YAML_DATA_DIR%/logs

set "OPENSEARCH_PATH_CONF=%W0_CONFIG_DIR%"

echo Starting OpenSearch W0 on http://127.0.0.1:%HARDWARE_SEARCH_PORT%
echo Config: %OPENSEARCH_PATH_CONF%
echo Data: %HARDWARE_SEARCH_DATA_DIR%\data
echo Logs: %HARDWARE_SEARCH_DATA_DIR%\logs
echo GC logs: %OPENSEARCH_HOME%\logs

call "%OPENSEARCH_HOME%\bin\opensearch.bat"

exit /b %ERRORLEVEL%
