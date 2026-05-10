@echo off
title PhysioSync - Installer
echo ============================================================
echo   PhysioSync Local Backend - Installer
echo ============================================================
echo.

:: ── Check Python ──────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo         Download from https://www.python.org/downloads/
    echo         Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)
echo [OK] Python found.
python --version

:: ── Create virtual environment ────────────────────────────────
echo.
echo [1/5] Creating virtual environment...
if not exist "venv" (
    python -m venv venv
    echo       Created venv/
) else (
    echo       venv/ already exists, skipping.
)
call venv\Scripts\activate.bat

:: ── Install Python dependencies ───────────────────────────────
echo.
echo [2/5] Installing Python dependencies...
pip install --upgrade pip >nul 2>&1
pip install -r requirements.txt
if errorlevel 1 (
    echo [WARN] Some packages may have failed. pyrealsense2 requires
    echo        the Intel RealSense SDK. Install it from:
    echo        https://github.com/IntelRealSense/librealsense/releases
)

:: ── Generate SSL certificates ─────────────────────────────────
echo.
echo [3/5] Setting up HTTPS certificates for cloud dashboard...
if not exist "certs" mkdir certs

if exist "certs\localhost.pem" (
    echo       Certificates already exist, skipping.
    goto :skip_certs
)

:: Download mkcert if not present
if not exist "mkcert.exe" (
    echo       Downloading mkcert...
    powershell -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://dl.filippo.io/mkcert/latest?for=windows/amd64' -OutFile 'mkcert.exe' }" 2>nul
    if not exist "mkcert.exe" (
        echo [WARN] Could not download mkcert. You can install it manually:
        echo        https://github.com/FiloSottile/mkcert/releases
        echo        Then run: mkcert -install
        echo                  mkcert -cert-file certs\localhost.pem -key-file certs\localhost-key.pem localhost 127.0.0.1
        goto :skip_certs
    )
)

echo       Installing local Certificate Authority...
mkcert.exe -install

echo       Generating localhost certificate...
mkcert.exe -cert-file certs\localhost.pem -key-file certs\localhost-key.pem localhost 127.0.0.1

if exist "certs\localhost.pem" (
    echo [OK]   HTTPS certificates generated in certs/
) else (
    echo [WARN] Certificate generation may have failed.
    echo        The backend will fall back to HTTP mode.
)

:skip_certs

:: ── Create output directories ─────────────────────────────────
echo.
echo [4/5] Creating output directories...
if not exist "output_excel" mkdir output_excel
echo       output_excel/ ready.

:: ── Add firewall rule ─────────────────────────────────────────
echo.
echo [5/5] Adding Windows Firewall rule for port 5000...
netsh advfirewall firewall show rule name="PhysioSync Backend" >nul 2>&1
if errorlevel 1 (
    netsh advfirewall firewall add rule name="PhysioSync Backend" dir=in action=allow protocol=TCP localport=5000 >nul 2>&1
    if errorlevel 1 (
        echo [WARN] Could not add firewall rule. Run as Administrator if needed.
    ) else (
        echo [OK]   Firewall rule added.
    )
) else (
    echo       Firewall rule already exists.
)

echo.
echo ============================================================
echo   Installation complete!
echo.
echo   To start the backend, double-click: start.bat
echo   Then open your dashboard at your Vercel URL.
echo ============================================================
pause
