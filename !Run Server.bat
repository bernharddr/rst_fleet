@echo off
echo ============================================
echo   RST Fleet Monitor — SERVER MODE
echo ============================================
echo.
echo   Mode server akan:
echo   - Poll data GPS setiap 10 detik
echo   - Simpan riwayat GPS ke database lokal
echo   - Generate laporan setiap 15 menit
echo   - Buka dashboard di browser otomatis
echo.
echo   Tekan Ctrl+C untuk menghentikan server.
echo   Jangan tutup jendela ini selama server berjalan.
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python tidak ditemukan. Jalankan "!Setup (first time only).bat" dulu.
    pause
    exit /b 1
)

:: Check .env
if not exist .env (
    echo ERROR: File .env tidak ditemukan.
    echo Buat file .env dengan isi:
    echo   GFLEET_USERNAME=...
    echo   GFLEET_PASSWORD=...
    echo   GFLEET_API_KEY=...
    pause
    exit /b 1
)

:: Install server dependencies if needed
python -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo Menginstall dependensi server...
    pip install fastapi "uvicorn[standard]"
    echo.
)

:: Open browser after 5 seconds (gives server time to start)
start /b cmd /c "timeout /t 5 /nobreak >nul && start http://localhost:8000"

echo Starting server at http://localhost:8000 ...
echo.
python -m server.app

pause
