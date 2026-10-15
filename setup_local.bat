@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_local.ps1"
if errorlevel 1 (
    pause
    exit /b 1
)
echo Fill DEEPSEEK_API_KEY in .env, put TXT files in knowledge, then run start_local.bat.
pause
