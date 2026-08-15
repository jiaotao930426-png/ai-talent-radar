$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $PSScriptRoot

if ($null -eq [Environment]::GetEnvironmentVariable("TALENT_RADAR_HOST", "Process")) {
    $env:TALENT_RADAR_HOST = "127.0.0.1"
}
if ($null -eq [Environment]::GetEnvironmentVariable("TALENT_RADAR_PORT", "Process")) {
    $env:TALENT_RADAR_PORT = "8765"
}
if ($null -eq [Environment]::GetEnvironmentVariable("TALENT_RADAR_DB", "Process")) {
    $env:TALENT_RADAR_DB = Join-Path $projectDir "data\talent_radar.db"
}

$exitCode = 1
Push-Location $projectDir
try {
    if ($env:TALENT_RADAR_PYTHON) {
        & $env:TALENT_RADAR_PYTHON "app.py"
    }
    elseif (Get-Command "py" -ErrorAction SilentlyContinue) {
        & py -3.11 "app.py"
    }
    else {
        & python "app.py"
    }
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $exitCode
