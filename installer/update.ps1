# =====================================================================
# Deinei System Updater Script (update.ps1)
# =====================================================================
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

# Force UTF-8 output for console
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue

$rootDir = Split-Path -Parent $PSScriptRoot
if (-not $rootDir) { $rootDir = (Get-Location).Path }
Set-Location $rootDir

# --- 0. Single Instance Guard ---
$updateMutexName = "Local\Deinei_Updater_Mutex"
$createdNewMutex = $false
try {
    $script:updateMutex = New-Object System.Threading.Mutex($true, $updateMutexName, [ref]$createdNewMutex)
} catch {
    $createdNewMutex = $false
}

if (-not $createdNewMutex) {
    Write-Host "======================================================================" -ForegroundColor Red
    Write-Host " [ERROR] アップデート処理が既に実行中です。" -ForegroundColor Red
    Write-Host " Another update instance is already running." -ForegroundColor DarkGray
    Write-Host "======================================================================`n" -ForegroundColor Red
    Start-Sleep -Seconds 3
    exit 2
}

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "  泥濘 (Deinei) アップデートツール / System Updater" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  [INFO] ユーザーデータの保護について:" -ForegroundColor Green
Write-Host "  以下の個人データ・モデル・設定は上書き・削除されずそのまま保持されます。"
Write-Host "    - 会話履歴・長期記憶 (backend\data\chats\, backend\data\chroma_db\)"
Write-Host "    - LLM / Vision / TTS モデル (*.gguf, *.safetensors)"
Write-Host "    - 配置済み Live2D / VRM アバターモデル (custom_public\deining\models\, vrm\)"
Write-Host "    - ハードウェア設定プロファイル (deining_profile.bat)"
Write-Host ""

# =====================================================================
# Step 1/4: 稼働中サーバープロセスの確認と停止
# =====================================================================
Write-Host "[Step 1/4] 稼働中の泥濘サーバープロセスを確認しています..." -ForegroundColor Yellow

$targetPorts = @(5000, 5001, 8080, 8090)
$stoppedCount = 0

try {
    $netConns = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $targetPorts -contains $_.LocalPort }
    $pidsToStop = @()
    if ($netConns) {
        $pidsToStop = @($netConns | Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { $_ -gt 4 })
    }

    foreach ($procId in $pidsToStop) {
        try {
            $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Host "  [STOP] 稼働中のプロセスを停止中: $($proc.ProcessName) (PID: $procId)" -ForegroundColor Cyan
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                $stoppedCount++
            }
        } catch {}
    }
} catch {}

if ($stoppedCount -gt 0) {
    Start-Sleep -Seconds 1
    Write-Host "  [OK] $stoppedCount 個のサーバープロセスを安全に停止しました。" -ForegroundColor Green
} else {
    Write-Host "  [OK] 稼働中のサーバープロセスはありません。" -ForegroundColor Green
}

# =====================================================================
# Step 2/4: 最新プログラム・配布アセットの取得 (Git または ZIP)
# =====================================================================
Write-Host "`n[Step 2/4] 最新ファイルの取得・確認を行っています..." -ForegroundColor Yellow

$gitDir = Join-Path $rootDir ".git"
$gitCmd = Get-Command git -ErrorAction SilentlyContinue

if ((Test-Path $gitDir) -and $gitCmd) {
    Write-Host "  [GIT] Git リポジトリ環境を検出しました。git pull を実行します..." -ForegroundColor Cyan
    try {
        & git fetch origin main 2>&1 | Out-Null
        $pullOut = & git pull --ff-only origin main 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [OK] Git から最新の差分を取得しました。" -ForegroundColor Green
            foreach ($line in $pullOut) {
                Write-Host "    $line" -ForegroundColor DarkGray
            }
        } else {
            Write-Host "  [WARN] git pull (--ff-only) をスキップしました (ローカルに未コミットの変更があるか、既に最新です)。" -ForegroundColor DarkYellow
            Write-Host "  [INFO] 現在のローカルファイルを元に実行環境の更新を続行します。" -ForegroundColor DarkGray
        }
    } catch {
        Write-Host "  [WARN] Git 通信に失敗しました。現在のローカルファイルを使用して更新を続行します。" -ForegroundColor DarkYellow
    }
} else {
    Write-Host "  [ZIP] ZIP 導入環境を検出しました。GitHub から最新版の取得を試みます..." -ForegroundColor Cyan
    $zipUrl = "https://github.com/kakushiarerukea/Deinei/archive/refs/heads/main.zip"
    $tempZip = Join-Path $env:TEMP "deinei_update_main.zip"
    $tempExtract = Join-Path $env:TEMP "deinei_update_extract"

    if (Test-Path $tempZip) { Remove-Item $tempZip -Force -ErrorAction SilentlyContinue }
    if (Test-Path $tempExtract) { Remove-Item $tempExtract -Recurse -Force -ErrorAction SilentlyContinue }

    $downloaded = $false
    try {
        if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
            & curl.exe -L -o $tempZip $zipUrl --silent --show-error --fail
            if ($LASTEXITCODE -eq 0 -and (Test-Path $tempZip)) { $downloaded = $true }
        } else {
            Invoke-WebRequest -Uri $zipUrl -OutFile $tempZip -UseBasicParsing
            if (Test-Path $tempZip) { $downloaded = $true }
        }
    } catch {
        $downloaded = $false
    }

    if ($downloaded -and ((Get-Item $tempZip).Length -gt 100000)) {
        Write-Host "  [EXTRACT] ダウンロードした最新アーカイブを展開・適用しています..." -ForegroundColor Cyan
        try {
            [System.IO.Compression.ZipFile]::ExtractToDirectory($tempZip, $tempExtract)
            $innerRoot = Get-ChildItem -Path $tempExtract -Directory | Select-Object -First 1
            if ($innerRoot) {
                $srcRoot = $innerRoot.FullName
                # ルート直下の主要ファイル更新 (deining_profile.bat は上書きしない)
                $rootFilesToUpdate = @(
                    "AGENTS.md", "README.md", "README.en.md", "LICENSE", "THIRD_PARTY_LICENSES.md",
                    "splash.html", "install.bat", "update.bat", "uninstall.bat"
                )
                foreach ($rf in $rootFilesToUpdate) {
                    $sFile = Join-Path $srcRoot $rf
                    if (Test-Path $sFile) {
                        Copy-Item -Path $sFile -Destination (Join-Path $rootDir $rf) -Force
                    }
                }

                # サブディレクトリの更新 (robocopy でパス長制限を回避し、ユーザーデータ chats / chroma_db / .venv を安全に除外)
                $dirsToSync = @(
                    "backend\data",
                    "backend\Irodori-TTS",
                    "backend\templates",
                    "backend\llama.cpp-b9873\custom_public",
                    "installer\templates",
                    "installer\wheels",
                    "docs",
                    "tests"
                )
                foreach ($relDir in $dirsToSync) {
                    $sDir = Join-Path $srcRoot $relDir
                    $dDir = Join-Path $rootDir $relDir
                    if (Test-Path $sDir) {
                        if (-not (Test-Path $dDir)) { New-Item -ItemType Directory -Path $dDir -Force | Out-Null }
                        & robocopy.exe $sDir $dDir /E /XO /XD chats chroma_db chroma_db_v2 .venv __pycache__ /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
                    }
                }

                # setup.ps1 / update.ps1 / uninstall.ps1 の更新
                foreach ($instScript in @("setup.ps1", "update.ps1", "uninstall.ps1")) {
                    $sInst = Join-Path $srcRoot "installer\$instScript"
                    if (Test-Path $sInst) {
                        Copy-Item -Path $sInst -Destination (Join-Path $rootDir "installer\$instScript") -Force
                    }
                }

                Write-Host "  [OK] GitHub から最新ファイルを適用しました。" -ForegroundColor Green
            }
        } catch {
            Write-Host "  [WARN] アーカイブの展開中に一部エラーが発生しました: $($_.Exception.Message)" -ForegroundColor DarkYellow
        } finally {
            Remove-Item $tempZip -Force -ErrorAction SilentlyContinue
            Remove-Item $tempExtract -Recurse -Force -ErrorAction SilentlyContinue
        }
    } else {
        Write-Host "  [INFO] オンライン取得をスキップしました。ローカルに展開済みのファイルを元に更新します。" -ForegroundColor DarkGray
    }
}

# =====================================================================
# Step 3/4: WebUI アセット・Jinja テンプレート・起動バッチの同期
# =====================================================================
Write-Host "`n[Step 3/4] WebUI アセットと起動スクリプトを実行環境へ同期しています..." -ForegroundColor Yellow

$llamaDir = Join-Path $rootDir "llama-b9873-bin-win-cuda-13.3-x64sandbox"
$bundledAssets = Join-Path $rootDir "backend\llama.cpp-b9873\custom_public"
$targetPublic = Join-Path $llamaDir "custom_public"
$bundledIndex = Join-Path $bundledAssets "index.html"

if (-not (Test-Path $llamaDir)) {
    Write-Host "  [WARN] 実行環境フォルダ ($llamaDir) がまだ作成されていません。" -ForegroundColor DarkYellow
    Write-Host "  [INFO] 初回セットアップが未完了の場合は install.bat を実行してください。" -ForegroundColor Yellow
} else {
    # 3.1 WebUI ビルド済みアセットの同期 (ユーザー配置の Live2D / VRM モデルや SDK は保持しつつ、古い _app バンドルを掃除)
    if (Test-Path $bundledIndex) {
        if (-not (Test-Path $targetPublic)) {
            New-Item -ItemType Directory -Path $targetPublic -Force | Out-Null
        }

        # 古いハッシュ付き JS/CSS バンドルが溜まらないよう _app ディレクトリのみクリーンアップしてからコピー
        $targetAppDir = Join-Path $targetPublic "_app"
        if (Test-Path $targetAppDir) {
            Remove-Item -Path $targetAppDir -Recurse -Force -ErrorAction SilentlyContinue
        }

        # robocopy でアバターモデル (models / vrm) を除外保護しつつ同期
        & robocopy.exe $bundledAssets $targetPublic /E /XO /XD models vrm /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
        Write-Host "  [OK] WebUI アセットを最新の custom_public へ同期しました。" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] 配布用 WebUI アセット ($bundledAssets) が見つかりませんでした。" -ForegroundColor DarkYellow
    }

    # 3.2 Jinja チャットテンプレートの配置
    $jinjaSrc = Join-Path $rootDir "backend\templates\gemma_chat_template.jinja"
    if (-not (Test-Path $jinjaSrc)) {
        $jinjaSrc = Join-Path $rootDir "backend\llama.cpp-b9873\models\templates\google-gemma-4-31B-it.jinja"
    }
    $jinjaDst = Join-Path $llamaDir "gemma_chat_template.jinja"
    if (Test-Path $jinjaSrc) {
        Copy-Item -Path $jinjaSrc -Destination $jinjaDst -Force
        Write-Host "  [OK] Jinja チャットテンプレートを更新しました。" -ForegroundColor Green
    }
}

# 3.3 起動バッチの更新
$mainLauncherSrc = Join-Path $rootDir "installer\templates\start_ai_servers.bat"
$mainLauncherDst = Join-Path $rootDir "start_ai_servers.bat"
if (Test-Path $mainLauncherSrc) {
    Copy-Item -Path $mainLauncherSrc -Destination $mainLauncherDst -Force
    Write-Host "  [OK] 起動バッチ (start_ai_servers.bat) を最新化しました。" -ForegroundColor Green
}

$subScriptsDir = Join-Path $rootDir "scripts"
if (-not (Test-Path $subScriptsDir)) {
    New-Item -ItemType Directory -Path $subScriptsDir -Force | Out-Null
}
foreach ($scriptName in @("start_ai_servers_lan.bat", "start_ai_servers_tailscale.bat", "deinei_doctor.bat")) {
    $srcPath = Join-Path $rootDir "installer\templates\$scriptName"
    $dstPath = Join-Path $subScriptsDir $scriptName
    if (Test-Path $srcPath) {
        Copy-Item -Path $srcPath -Destination $dstPath -Force
    }
}
Write-Host "  [OK] サブスクリプト (scripts\*.bat) を最新化しました。" -ForegroundColor Green

# =====================================================================
# Step 4/4: Python 依存パッケージの差分確認
# =====================================================================
Write-Host "`n[Step 4/4] Python 仮想環境のパッケージ差分を確認しています..." -ForegroundColor Yellow

$venvPython = Join-Path $rootDir "backend\.venv\Scripts\python.exe"
$reqFile = Join-Path $rootDir "backend\requirements.txt"

if ((Test-Path $venvPython) -and (Test-Path $reqFile)) {
    $rawUvCandidates = @(
        (Get-Command uv -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
        (Join-Path $rootDir "installer\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "bin\uv.exe"),
        (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe")
    )
    $uvCandidates = @($rawUvCandidates | Where-Object { $_ -and (Test-Path $_) })

    $cacheRoot = Join-Path $rootDir ".cache"
    if (Test-Path $cacheRoot) {
        $env:UV_CACHE_DIR = Join-Path $cacheRoot "uv"
        $env:PIP_CACHE_DIR = Join-Path $cacheRoot "pip"
    }

    if ($uvCandidates.Count -gt 0) {
        $uvExe = [string]$uvCandidates[0]
        & $uvExe pip install -r $reqFile --python $venvPython --quiet 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [OK] Python 依存パッケージは最新の状態です (uv 検証完了)。" -ForegroundColor Green
        } else {
            Write-Host "  [INFO] Python パッケージのオンライン確認をスキップしました (既存環境を維持します)。" -ForegroundColor DarkGray
        }
    } else {
        & $venvPython -m pip install -r $reqFile --quiet --no-warn-script-location 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [OK] Python 依存パッケージは最新の状態です (pip 検証完了)。" -ForegroundColor Green
        } else {
            Write-Host "  [INFO] Python パッケージのオンライン確認をスキップしました (既存環境を維持します)。" -ForegroundColor DarkGray
        }
    }
} else {
    Write-Host "  [INFO] Python 仮想環境 (.venv) が見つかりません。初回導入時は install.bat を実行してください。" -ForegroundColor Yellow
}

Write-Host "`n======================================================================" -ForegroundColor Cyan
Write-Host " [OK] アップデートが完了しました / Update Completed!" -ForegroundColor Green
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "  start_ai_servers.bat またはデスクトップのショートカットから起動できます。`n"

if ($script:updateMutex) {
    try {
        $script:updateMutex.ReleaseMutex()
        $script:updateMutex.Dispose()
    } catch {}
}
