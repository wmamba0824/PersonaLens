@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_local.ps1" -Mode Lan
pause
