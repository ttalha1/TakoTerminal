@echo off
chcp 65001 >nul
title TakoTerminal - GitHub Yukleme Sihirbazi
cls
echo ======================================================================
echo             🐙 TAKOTERMINAL - GITHUB YÜKLEME SİHİRBAZI
echo ======================================================================
echo.
echo Bu sihirbaz projenizi 1 dakika icinde GitHub hesabiniza yukler.
echo.
echo 1. Simdi tarayicinizda GitHub Yeni Depo sayfasi aciliyor...
echo    - Repository name: TakoTerminal yazin.
echo    - "Add README" ve "Add .gitignore" seceneklerini BOS BIRAKIN!
echo    - "Create repository" butonuna tiklayin.
echo.
start https://github.com/new
echo.
set /p REPO_URL="2. Olusturdugunuz GitHub Repo Linkini Buraya Yapistirin: "
if "%REPO_URL%"=="" (
    echo [HATA] Link girmediniz. Islem iptal edildi.
    pause
    exit /b
)

echo.
echo [1/3] Git ana dali 'main' olarak ayarlaniyor...
git branch -M main

echo [2/3] GitHub uzak baglantisi (origin) ekleniyor...
git remote remove origin 2>nul
git remote add origin %REPO_URL%

echo [3/3] Kodlar ve gorseller GitHub'a gonderiliyor (git push)...
git push -u origin main

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ======================================================================
    echo  TEBRİKLER! TakoTerminal basariyla GitHub'a yuklendi!
    echo ======================================================================
) else (
    echo.
    echo [BILGI] Eger GitHub oturum acma penceresi acildiysa giris yapin ve
    echo bu dosyayi tekrar calistirin.
)
echo.
pause
