@echo off
chcp 65001 > nul

:: LAN (同一Wi-Fi) 公開モードで統合ランチャーを起動
set "DEINEI_GATEWAY_MODE=lan"
if exist "%~dp0..\start_ai_servers.bat" (
    call "%~dp0..\start_ai_servers.bat" --mode lan %*
) else (
    call "%~dp0start_ai_servers.bat" --mode lan %*
)
exit /b 0
