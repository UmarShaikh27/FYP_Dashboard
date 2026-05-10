@echo off
title PhysioSync Backend
echo ============================================================
echo   PhysioSync Local Backend
echo ============================================================
echo.

:: Activate virtual environment
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo [ERROR] Virtual environment not found.
    echo         Please run install.bat first.
    pause
    exit /b 1
)

:: Check for SSL certs
if exist "certs\localhost.pem" (
    echo   Mode:     HTTPS ^(cloud dashboard compatible^)
    echo   Backend:  https://localhost:5000
) else (
    echo   Mode:     HTTP ^(local development only^)
    echo   Backend:  http://localhost:5000
)
echo.
echo   Press Ctrl+C to stop the backend.
echo ============================================================
echo.

python server.py
pause
