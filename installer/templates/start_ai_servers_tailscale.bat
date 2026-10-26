@echo off
chcp 65001 > nul

:: Tailscale 専用公開モードで統合ランチャーを起動
set "DEINEI_GATEWAY_MODE=tailscale"
set "DEINEI_EXTRA_ARGS=%*"
if exist "%~dp0..\start_ai_servers.bat" (
    call "%~dp0..\start_ai_servers.bat" --mode tailscale %*
) else (
    call "%~dp0start_ai_servers.bat" --mode tailscale %*
)
exit /b 0
