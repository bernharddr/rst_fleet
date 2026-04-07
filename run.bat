@echo off
echo ============================================
echo   Fleet Tracker - RST GPS Monitor
echo ============================================
echo.
echo   1. Update Google Sheet (LIVE)
echo   2. Test only - show result without updating sheet (DRY RUN)
echo   3. Exit
echo.
set /p choice="Choose [1/2/3]: "

if "%choice%"=="1" goto live
if "%choice%"=="2" goto dryrun
if "%choice%"=="3" exit /b 0

echo Invalid choice.
pause
exit /b 1

:live
echo.
echo Running live update...
python main.py
goto done

:dryrun
echo.
echo Running dry run (no sheet will be updated)...
python main.py --dry-run
goto done

:done
echo.
if errorlevel 1 (
    echo ERROR: Script failed. Check the output above for details.
) else (
    echo Done.
)
pause
