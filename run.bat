@echo off
echo ============================================
echo   RST Fleet Monitor
echo ============================================
echo.
echo   1. Run and open report
echo   2. Run (dry run / console preview)
echo   3. Open last report (without running)
echo   4. Exit
echo.
set /p choice="Choose [1/2/3/4]: "

if "%choice%"=="1" goto live
if "%choice%"=="2" goto dryrun
if "%choice%"=="3" goto openonly
if "%choice%"=="4" exit /b 0

echo Invalid choice.
pause
exit /b 1

:live
echo.
echo Fetching GPS data and generating report...
python main.py
if errorlevel 1 goto error
goto openreport

:dryrun
echo.
echo Running preview (console only)...
python main.py --dry-run
if errorlevel 1 goto error
goto openreport

:openreport
if exist fleet_report.html (
    echo.
    echo Opening report in browser...
    start fleet_report.html
) else (
    echo No report file found.
)
goto done

:openonly
if exist fleet_report.html (
    start fleet_report.html
) else (
    echo No report found. Run option 1 first.
    pause
)
exit /b 0

:error
echo.
echo ERROR: Something went wrong. Check the output above.
pause
exit /b 1

:done
echo.
echo Done.
pause
