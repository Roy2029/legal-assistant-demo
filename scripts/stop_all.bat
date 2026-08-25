@echo off
taskkill /FI "WINDOWTITLE eq legal-backend*" /F 2>nul
taskkill /FI "WINDOWTITLE eq legal-frontend*" /F 2>nul
echo 已尝试停止。
