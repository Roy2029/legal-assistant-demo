@echo off
echo Stopping Legal Assistant Demo ...
taskkill /FI "WINDOWTITLE eq legal-backend*" /F 2>nul
taskkill /FI "WINDOWTITLE eq legal-frontend*" /F 2>nul
taskkill /IM node.exe /F 2>nul
echo Done. If services are still running, close their windows manually.
pause
