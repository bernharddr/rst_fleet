@echo off
echo Creating distribution package...

REM Output zip name with date
set ZIP_NAME=rst-fleet-monitor_%DATE:~-4,4%%DATE:~-7,2%%DATE:~-10,2%.zip

REM Use PowerShell to create zip (works on Windows 10+)
powershell -Command ^
  "Compress-Archive -Force -Path ^
    'main.py','run.bat','setup.bat','requirements.txt', ^
    '.env','fleet_assignments.json', ^
    'config','gfleet','geocoding','output','sheets','state', ^
    'scheduler','vehicles.json', ^
    '.env.example' ^
  -DestinationPath '%ZIP_NAME%'"

if errorlevel 1 (
  echo.
  echo ERROR: Failed to create package.
  echo Make sure all files exist and try again.
  pause
  exit /b 1
)

echo.
echo Package created: %ZIP_NAME%
echo.
echo IMPORTANT: Recipients also need:
echo   - credentials.json  (Google service account, if using Sheets)
echo   - .env              (already included - contains credentials, keep private!)
echo.
pause
