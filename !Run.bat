@echo off
echo ============================================
echo   RST Fleet Monitor
echo ============================================
echo.
echo   1. Run sekali dan buka laporan
echo   2. Auto-run setiap X menit (biarkan jendela ini terbuka)
echo   3. Dry run (preview di konsol saja)
echo   4. Buka laporan terakhir (tanpa menjalankan)
echo   5. Keluar
echo.
set /p choice="Pilih [1/2/3/4/5]: "

if "%choice%"=="1" goto live
if "%choice%"=="2" goto autorun
if "%choice%"=="3" goto dryrun
if "%choice%"=="4" goto openonly
if "%choice%"=="5" exit /b 0

echo Pilihan tidak valid.
pause
exit /b 1

:live
echo.
echo Mengambil data GPS...
python main.py
if errorlevel 1 goto error
goto openreport

:autorun
echo.
set /p minutes="Jalankan setiap berapa menit? (contoh: 15) : "
if "%minutes%"=="" set minutes=15
echo.
echo Auto-run setiap %minutes% menit. Jangan tutup jendela ini.
echo Tekan Ctrl+C untuk berhenti.
echo.
python scheduler/runner.py --interval %minutes%
goto done

:dryrun
echo.
echo Menjalankan preview...
python main.py --dry-run
if errorlevel 1 goto error
goto openreport

:openreport
if exist fleet_report.html (
    echo.
    echo Membuka laporan di browser...
    start fleet_report.html
) else (
    echo File laporan belum ada.
)
goto done

:openonly
if exist fleet_report.html (
    start fleet_report.html
) else (
    echo Laporan belum ada. Jalankan pilihan 1 atau 2 terlebih dahulu.
    pause
)
exit /b 0

:error
echo.
echo ERROR: Ada yang salah. Periksa pesan di atas.
pause
exit /b 1

:done
echo.
echo Selesai.
pause
