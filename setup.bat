@echo off
cd /d "%~dp0"
echo Setting up Greentek Catalog...
echo.
set "PY=py -3"
%PY% --version >nul 2>nul || set "PY=python"
%PY% --version >nul 2>nul || (
  echo Python is not installed.
  echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH", then run this again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
echo.
choice /m "Is this PC also a SCRAPER PC (installs the browser tools)"
if errorlevel 2 goto done
".venv\Scripts\python.exe" -m pip install "playwright>=1.49"
".venv\Scripts\python.exe" -m playwright install chromium
:done
echo.
echo Setup finished.
echo   Server PC:    double-click start_server.bat
echo   Scraper PCs:  double-click start_scraper.bat
pause
