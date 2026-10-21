@echo off
cd /d "%~dp0"
title Greentek Catalog - SERVER (keep this window open)
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
start "" cmd /c "timeout /t 3 >NUL & start http://localhost:8000"
".venv\Scripts\python.exe" run.py
pause
