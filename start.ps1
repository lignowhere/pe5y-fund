param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimePython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$frontendRoot = Join-Path $projectRoot "frontend"
$buildId = Join-Path $frontendRoot ".next\BUILD_ID"
$logRoot = Join-Path $projectRoot "logs"

if (-not (Test-Path -LiteralPath $runtimePython)) {
    throw "Chua co .venv. Hay chay scripts\setup-runtime.ps1."
}
if (-not (Test-Path -LiteralPath $buildId)) {
    throw "Frontend chua build. Hay chay scripts\setup-runtime.ps1."
}
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

function Test-LocalPort([int]$Port) {
    return $null -ne (
        Get-NetTCPConnection -LocalPort $Port -State Listen `
            -ErrorAction SilentlyContinue |
        Select-Object -First 1
    )
}

function Test-BackendReady {
    try {
        $response = Invoke-RestMethod `
            -Uri "http://127.0.0.1:8002/api/health" `
            -TimeoutSec 2
        return $response.status -eq "ok"
    }
    catch {
        return $false
    }
}

function Test-FrontendReady {
    try {
        $response = Invoke-WebRequest `
            -Uri "http://127.0.0.1:3000" `
            -UseBasicParsing `
            -TimeoutSec 2
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

$backend = $null
$frontend = $null

if ((Test-LocalPort 8002) -and -not (Test-BackendReady)) {
    throw "Cong 8002 dang bi tien trinh khac chiem dung."
}
if ((Test-LocalPort 3000) -and -not (Test-FrontendReady)) {
    throw "Cong 3000 dang bi tien trinh khac chiem dung."
}

if (-not (Test-BackendReady)) {
    $backend = Start-Process `
        -FilePath $runtimePython `
        -ArgumentList "-m", "uvicorn", "backend.main:app",
                      "--host", "127.0.0.1", "--port", "8002" `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logRoot "backend.out.log") `
        -RedirectStandardError (Join-Path $logRoot "backend.err.log") `
        -PassThru
    Set-Content -LiteralPath (Join-Path $logRoot "backend.pid") `
        -Value $backend.Id
}

if (-not (Test-FrontendReady)) {
    $frontend = Start-Process `
        -FilePath "npm.cmd" `
        -ArgumentList "run", "start" `
        -WorkingDirectory $frontendRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logRoot "frontend.out.log") `
        -RedirectStandardError (Join-Path $logRoot "frontend.err.log") `
        -PassThru
    Set-Content -LiteralPath (Join-Path $logRoot "frontend.pid") `
        -Value $frontend.Id
}

$deadline = (Get-Date).AddSeconds(45)
while ((Get-Date) -lt $deadline) {
    if ($backend -and $backend.HasExited) {
        throw "Backend dung dot ngot. Kiem tra logs\backend.err.log."
    }
    if ($frontend -and $frontend.HasExited) {
        throw "Frontend dung dot ngot. Kiem tra logs\frontend.err.log."
    }
    if ((Test-BackendReady) -and (Test-FrontendReady)) {
        if (-not $NoBrowser) {
            Start-Process "http://localhost:3000"
        }
        Write-Output "PE5Y da san sang tai http://localhost:3000"
        exit 0
    }
    Start-Sleep -Milliseconds 500
}

throw "Qua thoi gian cho khoi dong. Kiem tra thu muc logs."
