@echo off
echo ============================================
echo   Fleet Tracker - First Time Setup
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python from https://python.org
    echo Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)
echo [OK] Python found.

REM Install dependencies
echo.
echo Installing required packages...
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: Failed to install packages.
    pause
    exit /b 1
)

REM Check .env file
echo.
if not exist ".env" (
    echo WARNING: .env file not found!
    echo Please create a .env file in this folder with your credentials.
    echo See .env.example for the required fields.
    pause
    exit /b 1
) else (
    echo [OK] .env file found.
)

REM Check credentials.json
if not exist "credentials.json" (
    echo WARNING: credentials.json not found!
    echo Please place your Google service account credentials file here.
) else (
    echo [OK] credentials.json found.
)

echo.
echo ============================================
echo   Setup complete! You can now run !Run.bat
echo ============================================
pause
