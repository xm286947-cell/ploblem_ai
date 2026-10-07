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
if not exist "%OPENSEARCH_HOME%\logs" mkdir "%OPENSEARCH_HOME%\logs"

if not exist "%OPENSEARCH_HOME%\opensearch-windows-install.bat" (
  echo SEARCH_ENGINE_WINDOWS_INSTALLER_NOT_FOUND: %OPENSEARCH_HOME%\opensearch-windows-install.bat
  exit /b 4
)

rem The official Windows ZIP entrypoint prepares the Security plugin and native
rem plugin runtime paths before launching OpenSearch. W0 uses that entrypoint,
rem but appends localhost-only settings and disables Security for the demo HTTP
rem adapter after the demo config is installed.
set "W0_CONFIG_FILE=%OPENSEARCH_HOME%\config\opensearch.yml"
set "W0_CONFIG_BASE=%HARDWARE_SEARCH_DATA_DIR%\opensearch.base.yml"
if not exist "%W0_CONFIG_BASE%" (
  copy /Y "%W0_CONFIG_FILE%" "%W0_CONFIG_BASE%" >nul
  if errorlevel 1 (
    echo SEARCH_CONFIG_BACKUP_FAILED
    exit /b 6
  )
)
copy /Y "%W0_CONFIG_BASE%" "%W0_CONFIG_FILE%" >nul
if errorlevel 1 (
  echo SEARCH_CONFIG_RESTORE_FAILED
  exit /b 7
)

set "YAML_DATA_DIR=%HARDWARE_SEARCH_DATA_DIR:\=/%"
>> "%W0_CONFIG_FILE%" echo.
>> "%W0_CONFIG_FILE%" echo # Hardware Knowledge W0 local-only overrides
>> "%W0_CONFIG_FILE%" echo discovery.type: single-node
>> "%W0_CONFIG_FILE%" echo network.host: 127.0.0.1
>> "%W0_CONFIG_FILE%" echo http.port: %HARDWARE_SEARCH_PORT%
>> "%W0_CONFIG_FILE%" echo plugins.security.disabled: true
>> "%W0_CONFIG_FILE%" echo path.data: %YAML_DATA_DIR%/data
>> "%W0_CONFIG_FILE%" echo path.logs: %YAML_DATA_DIR%/logs

echo Starting OpenSearch W0 on http://127.0.0.1:%HARDWARE_SEARCH_PORT%
echo Config: %W0_CONFIG_FILE%
echo Data: %HARDWARE_SEARCH_DATA_DIR%\data
echo Logs: %HARDWARE_SEARCH_DATA_DIR%\logs
echo GC logs: %OPENSEARCH_HOME%\logs

pushd "%OPENSEARCH_HOME%"
call "%OPENSEARCH_HOME%\opensearch-windows-install.bat"
set "SEARCH_EXIT_CODE=%ERRORLEVEL%"
popd

echo SEARCH_ENGINE_EXIT_CODE=%SEARCH_EXIT_CODE%
exit /b %SEARCH_EXIT_CODE%
