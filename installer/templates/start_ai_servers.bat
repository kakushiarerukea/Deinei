@echo off
chcp 65001 > nul

echo ========================================
echo    泥濘 AI Servers
echo ========================================

:: ========================================
:: インストール状態の確認 (未セットアップ時のガード)
:: ========================================
if not exist "%~dp0llama-b9873-bin-win-cuda-13.3-x64sandbox\llama-server.exe" (
    echo.
    echo ======================================================================
    echo  [エラー] Deinei システムのセットアップが完了していません。
    echo  先に install.bat を実行してインストールを行ってください。
    echo ======================================================================
    echo.
    pause
    exit /b 1
)

:: ========================================
:: 既存プロセスのポート競合自動クリーンアップ (8080, 5000, 5001)
:: ========================================
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ports = @(8080, 5000, 5001);" ^
  "$cleaned = $false;" ^
  "foreach ($p in $ports) {" ^
  "  $conns = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue;" ^
  "  foreach ($c in $conns) {" ^
  "    $pidToKill = $c.OwningProcess;" ^
  "    if ($pidToKill -and $pidToKill -gt 4) {" ^
  "      try {" ^
  "        $proc = Get-Process -Id $pidToKill -ErrorAction SilentlyContinue;" ^
  "        if ($proc -and ($proc.ProcessName -match 'python|llama-server')) {" ^
  "          Write-Host ('  - ポート ' + $p + ' の残存プロセス (PID: ' + $pidToKill + ', ' + $proc.ProcessName + ') を安全に終了しています...');" ^
  "          Stop-Process -Id $pidToKill -Force -ErrorAction SilentlyContinue;" ^
  "          $cleaned = $true;" ^
  "        }" ^
  "      } catch {}" ^
  "    }" ^
  "  }" ^
  "};" ^
  "if ($cleaned) { Start-Sleep -Seconds 1; }"

:: ========================================
:: プロファイル設定の読み込み
:: ========================================
if exist "%~dp0deining_profile.bat" (
    call "%~dp0deining_profile.bat"
)
if "%PROFILE_NAME%"=="" set "PROFILE_NAME=16GB (Default)"
if "%NCMOE%"=="" set "NCMOE=9"

echo  - 適用プロファイル: %PROFILE_NAME%
echo    * MoE CPUオフロード (-ncmoe): %NCMOE%
if "%MMPROJ_ARGS%"=="" (
    echo    * Visionエンコーダ: 無効化 [Flash Attention ^& Cache Reuse 有効]
) else (
    echo    * Visionエンコーダ: GPU有効 [%MMPROJ_ARGS%]
)
echo.

:: メインメモリのキャッシュ保持容量 (MiB) を指定 (例: 16384 = 16GB, 32768 = 32GB, -1 = 無制限)
set CACHE_RAM=16384

:: jinja テンプレートの自動配置 (未配置の場合は backend\templates 等から自動補完)
if not exist "%~dp0llama-b9873-bin-win-cuda-13.3-x64sandbox\gemma_chat_template.jinja" (
    if not exist "%~dp0llama-b9873-bin-win-cuda-13.3-x64sandbox" mkdir "%~dp0llama-b9873-bin-win-cuda-13.3-x64sandbox"
    if exist "%~dp0backend\templates\gemma_chat_template.jinja" (
        copy /y "%~dp0backend\templates\gemma_chat_template.jinja" "%~dp0llama-b9873-bin-win-cuda-13.3-x64sandbox\gemma_chat_template.jinja" > nul
    ) else if exist "%~dp0backend\llama.cpp-b9873\models\templates\google-gemma-4-31B-it.jinja" (
        copy /y "%~dp0backend\llama.cpp-b9873\models\templates\google-gemma-4-31B-it.jinja" "%~dp0llama-b9873-bin-win-cuda-13.3-x64sandbox\gemma_chat_template.jinja" > nul
    )
)

echo [1/3] Checking LLM Model and Starting LLM Server...
echo  - (※ LLM を最初に起動し、VRAMを最大限確保してからTTSを起動します)
set "FOUND_MODEL="
for /f "usebackq delims=" %%F in (`powershell -NoProfile -Command "Get-ChildItem '%~dp0llama-b9873-bin-win-cuda-13.3-x64sandbox\*.gguf', '%~dp0..\*.gguf' -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch 'mmproj' } | Select-Object -ExpandProperty FullName -First 1"`) do (
    set "FOUND_MODEL=%%F"
)

if not "%FOUND_MODEL%"=="" (
    echo  - 検出されたモデル: %FOUND_MODEL%
    start "LLM Server (8080)" /d "%~dp0llama-b9873-bin-win-cuda-13.3-x64sandbox" cmd /k ".\llama-server.exe -m ""%FOUND_MODEL%"" %MMPROJ_ARGS% --host 127.0.0.1 --no-slots --no-mmap -fit off -c %CACHE_RAM% -ngl 99 -ncmoe %NCMOE% --flash-attn on --cache-reuse 256 -np 1 --parallel 1 --path ./custom_public --port 8080 --chat-template-file gemma_chat_template.jinja"
) else (
    echo  - モデル自動検出なし: Melody1437 をデフォルト起動します
    start "LLM Server (8080)" /d "%~dp0llama-b9873-bin-win-cuda-13.3-x64sandbox" cmd /k ".\llama-server.exe -m Melody1437-27B.i1-IQ4_XS.gguf %MMPROJ_ARGS% --host 127.0.0.1 --no-slots --no-mmap -fit off -c %CACHE_RAM% -ngl 99 -ncmoe %NCMOE% --flash-attn on --cache-reuse 256 -np 1 --parallel 1 --path ./custom_public --port 8080 --chat-template-file gemma_chat_template.jinja"
)

:: 使用する Python 実行環境の自動特定 (パッケージ健全性チェック付き)
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PY_EXE="

:: 1. 統合仮想環境 (backend\.venv) の健全性チェック
if exist "%~dp0backend\.venv\Scripts\python.exe" (
    "%~dp0backend\.venv\Scripts\python.exe" -c "import flask, chromadb" >nul 2>&1
    if not errorlevel 1 set "PY_EXE=%~dp0backend\.venv\Scripts\python.exe"
)

:: 2. システム Python の健全性チェック
if "%PY_EXE%"=="" (
    python -c "import flask, chromadb" >nul 2>&1
    if not errorlevel 1 set "PY_EXE=python"
)

:: 3. 旧TTS仮想環境 (backend\Irodori-TTS\.venv) のチェック
if "%PY_EXE%"=="" if exist "%~dp0backend\Irodori-TTS\.venv\Scripts\python.exe" (
    "%~dp0backend\Irodori-TTS\.venv\Scripts\python.exe" -c "import fastapi" >nul 2>&1
    if not errorlevel 1 set "PY_EXE=%~dp0backend\Irodori-TTS\.venv\Scripts\python.exe"
)

:: 4. 最終フォールバック
if "%PY_EXE%"=="" set "PY_EXE=python"

echo [2/3] Starting TTS Server...
start "TTS Server" /d "%~dp0backend\Irodori-TTS" cmd /k ""%PY_EXE%" tts_server.py"

echo [3/3] Starting Flask Server...
start "Flask Server" /d "%~dp0backend\data" cmd /k ""%PY_EXE%" server.py"

echo ========================================
echo [Live2D Splash] スプラッシュ画面をブラウザで開いています...
echo ========================================
start "" "%~dp0splash.html"

echo.
echo ========================================
echo [Ready Check] 全サーバーのロード完了を待機しています...
echo  - LLM Server (8080/health)
echo  - Flask Server (5000)
echo  - TTS Server (5001)
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$llamaUrl = 'http://127.0.0.1:8080/health';" ^
  "$flaskUrl = 'http://127.0.0.1:5000/health';" ^
  "$ttsUrl   = 'http://127.0.0.1:5001/health';" ^
  "$start = Get-Date;" ^
  "Write-Host '[並行起動中] 全サーバーの準備状態を監視中 (確認スパン: 3秒)...';" ^
  "$lastStatus = '';" ^
  "$llamaReady = $false; $flaskReady = $false; $ttsReady = $false;" ^
  "while ($true) {" ^
  "  if (-not $llamaReady) {" ^
  "    try {" ^
  "      $r1 = Invoke-WebRequest -Uri $llamaUrl -UseBasicParsing -TimeoutSec 1 -ErrorAction SilentlyContinue;" ^
  "      if ($r1 -and $r1.StatusCode -eq 200) {" ^
  "        $j = $r1.Content | ConvertFrom-Json -ErrorAction SilentlyContinue;" ^
  "        if ($j -and $j.status -eq 'ok') { $llamaReady = $true; }" ^
  "      }" ^
  "    } catch {}" ^
  "  }" ^
  "  if (-not $flaskReady) {" ^
  "    try {" ^
  "      $r2 = Invoke-WebRequest -Uri $flaskUrl -UseBasicParsing -TimeoutSec 1 -ErrorAction SilentlyContinue;" ^
  "      if ($r2 -and $r2.StatusCode -eq 200) { $flaskReady = $true; }" ^
  "    } catch {}" ^
  "  }" ^
  "  if (-not $ttsReady) {" ^
  "    try {" ^
  "      $r3 = Invoke-WebRequest -Uri $ttsUrl -UseBasicParsing -TimeoutSec 1 -ErrorAction SilentlyContinue;" ^
  "      if ($r3 -and $r3.StatusCode -eq 200) { $ttsReady = $true; }" ^
  "    } catch {}" ^
  "  }" ^
  "  $sLlm   = if ($llamaReady) { 'ONLINE' } else { 'VRAMロード中' };" ^
  "  $sTts   = if ($ttsReady)   { 'ONLINE' } else { '起動準備中' };" ^
  "  $sFlask = if ($flaskReady) { 'ONLINE' } else { '起動準備中' };" ^
  "  $statusLine = ('LLM(8080): [' + $sLlm + '] | TTS(5001): [' + $sTts + '] | Gateway(5000): [' + $sFlask + ']');" ^
  "  if ($statusLine -ne $lastStatus) {" ^
  "    Write-Host (' -> ' + $statusLine) -ForegroundColor Cyan;" ^
  "    $lastStatus = $statusLine;" ^
  "  }" ^
  "  if ($llamaReady -and $flaskReady -and $ttsReady) { break; }" ^
  "  Start-Sleep -Seconds 3;" ^
  "}" ^
  "$elapsed = [math]::Round(((Get-Date) - $start).TotalSeconds, 1);" ^
  "Write-Host '';" ^
  "Write-Host ('[SUCCESS] 全サーバーの並行準備が完了しました！ (起動所要時間: ' + $elapsed + '秒)') -ForegroundColor Green;"

echo.
echo ========================================
echo [SUCCESS] 全サーバーが正常稼働中です！
echo  - 対話UI: http://127.0.0.1:8080
echo  - (スプラッシュ画面から自動で対話画面へ接続されます)
echo ========================================
pause
