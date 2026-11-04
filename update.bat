@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: ----------------------------------------------------------------------
:: ZIP未解凍 / 一時フォルダ実行検知ガード
:: ----------------------------------------------------------------------
echo "%~dp0" | findstr /i "\\AppData\\Local\\Temp\\ \\Temporary Internet Files\\" > nul
if %errorlevel% equ 0 (
    echo ======================================================================
    echo  [ERROR] ZIPファイルが解凍（展開）されていません
    echo ======================================================================
    echo.
    echo  ZIPファイルの中身を直接開いた状態で実行している可能性があります。
    echo  展開された通常のフォルダ内にある update.bat を実行してください。
    echo.
    echo ======================================================================
    pause
    exit /b 1
)

if not exist "%~dp0installer\update.ps1" (
    echo ======================================================================
    echo  [ERROR] アップデートスクリプト ^(installer\update.ps1^) が見つかりません。
    echo ======================================================================
    echo.
    pause
    exit /b 1
)

echo ======================================================================
echo    Deinei System Updater
echo ======================================================================
echo.

:: update.bat 自身がアップデートで上書きされても cmd.exe のファイルポインタずれが起きないよう1行で完結させる
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; & '%~dp0installer\update.ps1'" & if not errorlevel 2 (echo. & pause) & exit /b 0
