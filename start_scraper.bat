@echo off
cd /d "%~dp0"
title Greentek Scraper (keep this window open)
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scraper\scraper_app.py
if errorlevel 1 pause
