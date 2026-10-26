@echo off
chcp 65001 > nul

:: 統合ランチャーの Doctor 接続診断ウィンドウを起動
if exist "%~dp0..\start_ai_servers.bat" (
    call "%~dp0..\start_ai_servers.bat" --doctor --no-autostart --no-browser %*
) else (
    call "%~dp0start_ai_servers.bat" --doctor --no-autostart --no-browser %*
)
exit /b 0
