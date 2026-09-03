param(
    [double]$Capital = 5000000000,
    [string]$Output = ".\output\strategy-snapshot-backtest.json"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python -ErrorAction Stop).Source

Push-Location $projectRoot
try {
    & $python -m backend.backtest.rebuild_snapshots `
        --capital $Capital `
        --output $Output
    if ($LASTEXITCODE -ne 0) {
        throw "Snapshot rebuild failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
