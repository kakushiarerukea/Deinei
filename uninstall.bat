@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist "%~dp0installer\uninstall.ps1" (
    echo ======================================================================
    echo  [ERROR] アンインストーラー本体 ^(installer\uninstall.ps1^) が見つかりません。
    echo ======================================================================
    echo.
    pause
    exit /b 1
)

echo ======================================================================
echo    Deinei System Uninstaller / Cleanup Tool
echo ======================================================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; & '%~dp0installer\uninstall.ps1'"

echo.
pause
