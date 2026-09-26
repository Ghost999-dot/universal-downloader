@echo off
title Build Universal Downloader EXE
cd /d "%~dp0"

echo ============================================================
echo  Rebuilding Universal_Downloader.exe from source\
echo ============================================================
echo.
echo Installing build tools (one-time)...
python -m pip install --upgrade pyinstaller flask yt-dlp pillow curl_cffi telethon

echo.
echo Refreshing the app icon...
python make_icon.py

echo.
echo Building... this can take a few minutes.
python -m PyInstaller --clean --noconfirm --onefile --windowed --name "Universal_Downloader" ^
    --icon "icon.ico" ^
    --add-data "static;static" ^
    --add-data "icon.ico;." ^
    --collect-all yt_dlp ^
    --collect-all curl_cffi ^
    --collect-all telethon ^
    "launcher.py"

echo.
echo Stopping any running instance so the exe can be replaced...
taskkill /F /IM Universal_Downloader.exe >nul 2>&1
ping -n 3 127.0.0.1 >nul

if exist "dist\Universal_Downloader.exe" (
    move /Y "dist\Universal_Downloader.exe" "..\Universal_Downloader.exe" >nul
    rmdir /s /q build dist 2>nul
    del /q "Universal_Downloader.spec" 2>nul
    echo ============================================================
    echo  DONE. Updated  ..\Universal_Downloader.exe
    echo ============================================================
) else (
    echo Build did not produce an exe. Scroll up for the error.
)

rem  pause only for a normal double-click; "Build EXE.bat auto" skips it for scripts
if not "%~1"=="auto" pause
