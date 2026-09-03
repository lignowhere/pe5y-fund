param(
    [switch]$SkipFrontendBuild
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $projectRoot ".venv"
$python = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    & python -m venv $venv
}

& $python -m pip install --upgrade pip
& $python -m pip install -r (Join-Path $projectRoot "requirements.txt")

if (-not $SkipFrontendBuild) {
    Push-Location (Join-Path $projectRoot "frontend")
    try {
        & npm ci
        & npm run build
    }
    finally {
        Pop-Location
    }
}

Write-Output "Runtime da san sang: $venv"
