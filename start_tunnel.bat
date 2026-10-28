@echo off
title FlashCart Cloudflare Public Tunnel
echo ========================================================
echo   Starting FlashCart Public Live Tunnel (Port 8501)
echo ========================================================
echo.
if not exist "%~dp0cloudflared.exe" (
    echo Downloading cloudflared.exe...
    curl.exe -L "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -o "%~dp0cloudflared.exe"
)
"%~dp0cloudflared.exe" tunnel --url http://localhost:8501
pause
