@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if errorlevel 1 (
  echo.
  echo PE5Y khong khoi dong duoc. Xem thong bao o tren.
  pause
)
endlocal
