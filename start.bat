@echo off
title PE5Y Fund System
echo ============================================
echo   PE5Y Fund System - Starting...
echo ============================================
echo.
echo   Backend:  http://localhost:8002
echo   Frontend: http://localhost:3000
echo.
start "PE5Y Backend (FastAPI)" cmd /k "cd /d %~dp0 && uvicorn backend.main:app --host 127.0.0.1 --port 8002 --reload"
start "PE5Y Frontend (Next.js)" cmd /k "cd /d %~dp0frontend && npm run dev"
echo Both servers launched in separate windows.
timeout /t 3 >nul
