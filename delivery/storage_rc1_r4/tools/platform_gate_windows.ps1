param(
    [Parameter(Mandatory=$true)]
    [string]$Package
)

$ErrorActionPreference = "Stop"
$resolved = (Resolve-Path -LiteralPath $Package).Path
$gateRoot = Join-Path $env:TEMP "Storage R4 Gate Space"
$extract = Join-Path $gateRoot "fresh extract"
if (Test-Path -LiteralPath $gateRoot) {
    Remove-Item -LiteralPath $gateRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $extract -Force | Out-Null

Expand-Archive -LiteralPath $resolved -DestinationPath $extract -Force
$root = Join-Path $extract "STORAGE_PRODUCT_MVP_RC1"
if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    throw "package root missing: $root"
}

$env:STORAGE_RELEASE_GATE_CHECK_ONLY = "1"
Push-Location (Split-Path -Parent $root)
try {
    & (Join-Path $root "run_windows.bat")
    $rc = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($rc -ne 0) {
    throw "Windows official launcher failed: exit=$rc"
}

Write-Output "WINDOWS_STANDARD_ZIP_EXTRACT=PASS"
Write-Output "WINDOWS_PATH_WITH_SPACES=PASS"
Write-Output "WINDOWS_CWD_SWITCH=PASS"
Write-Output "WINDOWS_PYTHON_CONFIG_LOCATION=PASS"
Write-Output "WINDOWS_OFFICIAL_LAUNCHER=PASS"
