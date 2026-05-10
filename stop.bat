@echo off
title PhysioSync - Stopping
echo ============================================================
echo   Stopping PhysioSync Backend...
echo ============================================================
echo.

:: Kill Python processes running server.py
taskkill /F /FI "WINDOWTITLE eq PhysioSync Backend*" >nul 2>&1
taskkill /F /FI "IMAGENAME eq python.exe" /FI "WINDOWTITLE eq PhysioSync*" >nul 2>&1

echo   Backend stopped.
echo.
timeout /t 2 >nul
