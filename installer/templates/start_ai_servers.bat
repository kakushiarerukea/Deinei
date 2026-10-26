@echo off
chcp 65001 > nul

:: ======================================================================
:: 泥濘 (Deinei) 統合サーバーランチャー 起動スクリプト
:: ======================================================================

:: 1. インストール状態の確認 (未セットアップ時のガード)
if not exist "%~dp0llama-b9873-bin-win-cuda-13.3-x64sandbox\llama-server.exe" (
    echo.
    echo ======================================================================
    echo  [ERROR] Deinei システムのセットアップが完了していません。
    echo  先に install.bat を実行してインストールを行ってください。
    echo ======================================================================
    echo.
    pause
    exit /b 1
)

:: 2. 統合キャッシュ環境変数の設定 (.cache 配下に集約) と Python 実行環境の自動特定
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "UV_CACHE_DIR=%~dp0.cache\uv"
set "UV_PYTHON_INSTALL_DIR=%~dp0.cache\uv_python"
set "PIP_CACHE_DIR=%~dp0.cache\pip"
set "HF_HOME=%~dp0.cache\huggingface"
set "TORCH_HOME=%~dp0.cache\torch"
set "PY_EXE="
set "PYW_EXE="

:: (A) 統合仮想環境 (backend\.venv) の健全性チェック
if exist "%~dp0backend\.venv\Scripts\python.exe" (
    "%~dp0backend\.venv\Scripts\python.exe" -c "import flask, chromadb" >nul 2>&1
    if not errorlevel 1 (
        set "PY_EXE=%~dp0backend\.venv\Scripts\python.exe"
    )
)

:: (B) システム Python の健全性チェック
if "%PY_EXE%"=="" (
    python -c "import flask, chromadb" >nul 2>&1
    if not errorlevel 1 (
        for /f "usebackq delims=" %%P in (`python -c "import sys; print(sys.executable)"`) do (
            set "PY_EXE=%%P"
        )
    )
)

:: (C) 旧TTS仮想環境 (backend\Irodori-TTS\.venv) のチェック
if "%PY_EXE%"=="" if exist "%~dp0backend\Irodori-TTS\.venv\Scripts\python.exe" (
    "%~dp0backend\Irodori-TTS\.venv\Scripts\python.exe" -c "import fastapi" >nul 2>&1
    if not errorlevel 1 (
        set "PY_EXE=%~dp0backend\Irodori-TTS\.venv\Scripts\python.exe"
    )
)

:: (D) 最終フォールバック
if "%PY_EXE%"=="" set "PY_EXE=python"

:: 同一ディレクトリの pythonw.exe (tkinter 対応時のみ) を特定
for /f "usebackq delims=" %%W in (`"%PY_EXE%" -c "import sys, os, tkinter; p = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'); print(p if os.path.isfile(p) else '')" 2^>nul`) do (
    set "PYW_EXE=%%W"
)

:: 3. 環境変数からの起動モード引き継ぎ
set "MODE_ARGS="
if /i "%DEINEI_GATEWAY_MODE%"=="lan" set "MODE_ARGS=--mode lan"
if /i "%DEINEI_GATEWAY_MODE%"=="tailscale" set "MODE_ARGS=--mode tailscale"

:: 4. CLI モード指定判定 (--cli が含まれる場合)
echo %* | findstr /i "\-\-cli" >nul 2>&1
if not errorlevel 1 (
    "%PY_EXE%" -X utf8 -u "%~dp0backend\data\deinei_launcher.py" %MODE_ARGS% %DEINEI_EXTRA_ARGS% %*
    exit /b %errorlevel%
)

:: 5. デスクトップ GUI ランチャーの起動 (コンソールウィンドウを残さず起動)
if not "%PYW_EXE%"=="" (
    start "" "%PYW_EXE%" -X utf8 "%~dp0backend\data\deinei_launcher.py" %MODE_ARGS% %DEINEI_EXTRA_ARGS% %*
) else (
    start "" /min "%PY_EXE%" -X utf8 -u "%~dp0backend\data\deinei_launcher.py" %MODE_ARGS% %DEINEI_EXTRA_ARGS% %*
)

exit /b 0
