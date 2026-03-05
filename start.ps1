Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  PE5Y Fund System - Starting..." -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Backend:  http://localhost:8002" -ForegroundColor Green
Write-Host "  Frontend: http://localhost:3000" -ForegroundColor Green
Write-Host ""

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Start-Process -FilePath "cmd" -ArgumentList "/k", "cd /d $root && uvicorn backend.main:app --host 127.0.0.1 --port 8002 --reload --reload-dir backend" -WindowStyle Normal
Start-Process -FilePath "cmd" -ArgumentList "/k", "cd /d $root\frontend && npm run dev" -WindowStyle Normal

Write-Host "Both servers launched in separate windows." -ForegroundColor Yellow
