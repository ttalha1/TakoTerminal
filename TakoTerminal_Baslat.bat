@echo off
chcp 65001 >nul
title TakoTerminal - Akıllı Baskı Kiosk Sistemi
color 0b
echo ======================================================================
echo           🖨️  TAKOTERMINAL - AKILLI BASKI KİOSK SİSTEMİ
echo ======================================================================
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [HATA] Python sanal ortamı (.venv) bulunamadı!
    echo Lütfen 'python -m venv .venv' ve 'pip install -r requirements.txt' çalıştırınız.
    pause
    exit /b
)

echo [1/2] Sunucu ve Cloudflare 4G/5G Tüneli hazırlanıyor...
echo [2/2] Kiosk paneli tarayıcınızda açılıyor...
echo.
timeout /t 2 /nobreak >nul
start http://localhost:8000/kiosk
".venv\Scripts\python.exe" run_kiosk.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [HATA] TakoTerminal bir hata ile kapandı!
    pause
)
