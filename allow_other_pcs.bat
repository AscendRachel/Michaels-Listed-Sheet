@echo off
REM Lets other office PCs open the Greentek Catalog on this PC (port 8000).
net session >nul 2>&1
if errorlevel 1 (
  echo Asking Windows for permission...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
netsh advfirewall firewall delete rule name="Greentek Catalog" >nul 2>&1
netsh advfirewall firewall add rule name="Greentek Catalog" dir=in action=allow protocol=TCP localport=8000 profile=any
if errorlevel 1 (
  echo Something went wrong. Take a screenshot of this window and send it.
) else (
  echo.
  echo Done. Other office PCs can now open the app on this PC.
)
echo.
pause
