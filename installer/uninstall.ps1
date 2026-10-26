# =====================================================================
# Deinei System Uninstaller & Cleanup Script
# =====================================================================
$ErrorActionPreference = "Continue"

# Force UTF-8 output for console
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$rootDir = Split-Path -Parent $PSScriptRoot
Set-Location $rootDir

# =====================================================================
# Helper: Prompt-YesNo (Input Buffer Safe / Beginner Friendly)
# =====================================================================
function Prompt-YesNo {
    param(
        [string]$PromptJa,
        [string]$PromptEn,
        [bool]$DefaultYes = $false
    )

    try {
        while ([System.Console]::KeyAvailable) {
            [void][System.Console]::ReadKey($true)
        }
    } catch {}

    $defaultGuideJa = if ($DefaultYes) { "[Y] はい (そのままEnterキーで決定)" } else { "[N] いいえ・残す (推奨 / そのままEnterキーで決定)" }
    $defaultGuideEn = if ($DefaultYes) { "Default: [Y] Yes (Press Enter to confirm)" } else { "Default: [N] No / Keep (Press Enter to confirm)" }

    while ($true) {
        Write-Host ""
        Write-Host "  $PromptJa" -ForegroundColor Cyan
        Write-Host "  $PromptEn" -ForegroundColor DarkCyan
        Write-Host "  >> $defaultGuideJa" -ForegroundColor Yellow
        Write-Host "     $defaultGuideEn" -ForegroundColor DarkGray
        $raw = Read-Host "  入力 / Choice [Y: はい / N: いいえ]"
        $inputClean = if ($raw) { $raw.Trim().ToLower() } else { "" }

        if ($inputClean -eq "") {
            return $DefaultYes
        } elseif ($inputClean -eq "y" -or $inputClean -eq "yes") {
            return $true
        } elseif ($inputClean -eq "n" -or $inputClean -eq "no") {
            return $false
        } else {
            Write-Host "  [INFO] 'y' (はい) または 'n' (いいえ) を入力して Enter を押してください。" -ForegroundColor DarkYellow
        }
    }
}

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "  泥濘 (Deinei) アンインストール・クリーンアップツール" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  [INFO] 安全設計について:" -ForegroundColor Green
Write-Host "  他のAIツール (Stable Diffusion / ComfyUI 等) の環境を破損させないため、"
Write-Host "  PCの共通領域 (システムPython、%USERPROFILE%\.cache、Tailscale本体等) には"
Write-Host "  一切変更を加えません。泥濘フォルダ内のデータと専用ショートカットのみを対象とします。"
Write-Host ""

# =====================================================================
# Detect User Avatars (Live2D & VRM Models)
# =====================================================================
$llamaDir = Join-Path $rootDir "llama-b9873-bin-win-cuda-13.3-x64sandbox"
$deiningPublicDir = Join-Path $llamaDir "custom_public\deining"
$hasUserLive2d = $false
$hasUserVrm = $false
$hasUserAvatars = $false

if (Test-Path $deiningPublicDir) {
    $live2dFiles = Get-ChildItem -Path $deiningPublicDir -Filter "*.model3.json" -Recurse -ErrorAction SilentlyContinue
    if ($live2dFiles -and $live2dFiles.Count -gt 0) {
        $hasUserLive2d = $true
    }
    $vrmFiles = Get-ChildItem -Path $deiningPublicDir -Filter "*.vrm" -Recurse -ErrorAction SilentlyContinue
    if ($vrmFiles -and $vrmFiles.Count -gt 0) {
        $hasUserVrm = $true
    }
    if ($hasUserLive2d -or $hasUserVrm) {
        $hasUserAvatars = $true
    }
}

# =====================================================================
# Step 0: Select What to Keep / Delete
# =====================================================================
Write-Host "----------------------------------------------------------------------" -ForegroundColor DarkGray
Write-Host " [選択 1/3] 会話履歴・長期記憶データの扱い" -ForegroundColor Yellow
Write-Host "  対象: ブラウザ内の会話ツリー (IndexedDB) / backend\data\chats\ / chroma_db\"
Write-Host "  ※同一PC内に別の泥濘フォルダがある場合、ブラウザ内データ (http://127.0.0.1:8080) は" -ForegroundColor DarkGray
Write-Host "    ブラウザ上で共有されているため、別フォルダ側で継続利用する場合は N (残す) を選んでください。" -ForegroundColor DarkGray
$deleteHistory = Prompt-YesNo `
    -PromptJa "会話履歴と長期記憶データも削除しますか？ (残す場合は N)" `
    -PromptEn "Delete conversation history and long-term memory DB? (N to keep)" `
    -DefaultYes $false

Write-Host ""
Write-Host "----------------------------------------------------------------------" -ForegroundColor DarkGray
Write-Host " [選択 2/3] WebUI上で変更したパラメータ・設定・Cookieの扱い" -ForegroundColor Yellow
Write-Host "  対象: ブラウザ内の設定 (システムプロンプト、Temperature等のパラメータ、"
Write-Host "        Live2D配置・当たり判定設定、Cookie) および deining_profile.bat"
Write-Host "  ※ブラウザ内の設定も http://127.0.0.1:8080 単位で共有されています。" -ForegroundColor DarkGray
$deleteSettings = Prompt-YesNo `
    -PromptJa "WebUIのパラメータ・設定・Cookieも削除(初期化)しますか？ (残す場合は N)" `
    -PromptEn "Delete WebUI parameters, settings, and cookies? (N to keep)" `
    -DefaultYes $false

$deleteAvatars = $false
if ($hasUserAvatars) {
    $avatarTypes = @()
    if ($hasUserLive2d) { $avatarTypes += "Live2D" }
    if ($hasUserVrm) { $avatarTypes += "VRM" }
    $avatarTypesStr = $avatarTypes -join " / "

    Write-Host ""
    Write-Host "----------------------------------------------------------------------" -ForegroundColor DarkGray
    Write-Host " [選択 3/3] 導入済みアバターモデル ($avatarTypesStr) の扱い" -ForegroundColor Yellow
    Write-Host "  対象: custom_public\deining\ 内に配置されたアバターモデルデータ (Live2D / VRM)"
    $deleteAvatars = Prompt-YesNo `
        -PromptJa "導入済みのモデルファイル ($avatarTypesStr) も削除しますか？ (残す場合は N)" `
        -PromptEn "Delete imported avatar character models ($avatarTypesStr)? (N to keep)" `
        -DefaultYes $false
}

# =====================================================================
# Summary & Final Confirmation
# =====================================================================
Write-Host ""
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "  アンインストール実行内容の確認" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "  [削除される項目 (共通)]:" -ForegroundColor Yellow
Write-Host "    - 統合キャッシュフォルダ (.cache\ / .uv_cache\)"
Write-Host "    - Python 仮想環境 (backend\.venv\) およびローカル uv.exe"
Write-Host "    - LLM 実行バイナリ・CUDA DLL・GGUFモデル重み (sandbox)"
Write-Host "    - TTS 音声合成モデル重み (backend\Irodori-TTS\models\model.safetensors)"
Write-Host "    - デスクトップショートカットおよび生成された起動バッチ群"

Write-Host ""
Write-Host "  [ユーザーデータの扱い]:" -ForegroundColor Yellow
if ($deleteHistory) {
    Write-Host "    - 会話履歴・長期記憶 (chats / chroma_db / IndexedDB): [削除する]" -ForegroundColor Red
} else {
    Write-Host "    - 会話履歴・長期記憶 (chats / chroma_db / IndexedDB): [残す (保持)]" -ForegroundColor Green
}

if ($deleteSettings) {
    Write-Host "    - WebUI パラメータ・設定・Cookie・プロファイル:       [削除する]" -ForegroundColor Red
} else {
    Write-Host "    - WebUI パラメータ・設定・Cookie・プロファイル:       [残す (保持)]" -ForegroundColor Green
}

if ($hasUserAvatars) {
    $avatarTypesStr = if ($hasUserLive2d -and $hasUserVrm) { "Live2D / VRM" } elseif ($hasUserLive2d) { "Live2D" } else { "VRM" }
    if ($deleteAvatars) {
        Write-Host "    - 導入済みアバターモデル ($avatarTypesStr):               [削除する]" -ForegroundColor Red
    } else {
        Write-Host "    - 導入済みアバターモデル ($avatarTypesStr):               [残す (保持)]" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "  [保護される項目 (変更なし)]:" -ForegroundColor Green
Write-Host "    - PC共通領域 (システムPython / 共通HuggingFaceキャッシュ / Tailscale等)"

$confirmRun = Prompt-YesNo `
    -PromptJa "上記の内容でアンインストールを実行しますか？" `
    -PromptEn "Proceed with uninstallation?" `
    -DefaultYes $false

if (-not $confirmRun) {
    Write-Host ""
    Write-Host "  [INFO] アンインストールをキャンセルしました。変更は行われていません。" -ForegroundColor Yellow
    exit 0
}

# =====================================================================
# Step 1: Stop Running Deinei Server Processes
# =====================================================================
Write-Host "`n[Step 1/4] 稼働中の泥濘サーバープロセスを確認・停止しています..." -ForegroundColor Yellow

$rootNormalized = $rootDir.ToLower().TrimEnd('\')
$rootWithSlash = $rootNormalized + "\"
$targetPorts = @(8080, 5000, 5001, 8090)

foreach ($p in $targetPorts) {
    $conns = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $pidToKill = $c.OwningProcess
        if ($pidToKill -and $pidToKill -gt 4 -and $pidToKill -ne $PID) {
            $proc = Get-Process -Id $pidToKill -ErrorAction SilentlyContinue
            if ($proc -and ($proc.ProcessName -match "python|llama-server")) {
                $cimProc = Get-CimInstance Win32_Process -Filter "ProcessId = $pidToKill" -ErrorAction SilentlyContinue
                $procExe = if ($cimProc -and $cimProc.ExecutablePath) { $cimProc.ExecutablePath.ToLower() } else { "" }
                $procCmd = if ($cimProc -and $cimProc.CommandLine) { $cimProc.CommandLine.ToLower() } else { "" }
                $isOurs = ($procExe -and $procExe.StartsWith($rootWithSlash)) -or ($procCmd -and $procCmd.Contains($rootWithSlash)) -or ($procCmd -and $procCmd.Contains("deinei"))

                if ($isOurs) {
                    Write-Host "  [STOP] ポート $p の泥濘プロセスを停止中: $($proc.ProcessName) (PID: $pidToKill)" -ForegroundColor Cyan
                    & cmd.exe /c "taskkill /F /T /PID $pidToKill" 2>&1 | Out-Null
                } else {
                    Write-Host "  [SKIP] ポート $p を使用中のプロセス (PID: $pidToKill, $($proc.ProcessName)) は泥濘配下ではないため安全のためスキップしました。" -ForegroundColor DarkYellow
                }
            }
        }
    }
}

# Stop any remaining python / llama-server processes whose executable lives inside this $rootDir
$procs = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%' OR Name LIKE 'llama-server%'" -ErrorAction SilentlyContinue
foreach ($pr in $procs) {
    if ($pr.ProcessId -eq $PID) { continue }
    $exePath = if ($pr.ExecutablePath) { $pr.ExecutablePath.ToLower() } else { "" }
    $cmdLine = if ($pr.CommandLine) { $pr.CommandLine.ToLower() } else { "" }
    if (($exePath -and $exePath.StartsWith($rootWithSlash)) -or ($cmdLine -and $cmdLine.Contains($rootWithSlash))) {
        Write-Host "  [STOP] このフォルダ配下のプロセスを停止中: $($pr.Name) (PID: $($pr.ProcessId))" -ForegroundColor Cyan
        & cmd.exe /c "taskkill /F /T /PID $($pr.ProcessId)" 2>&1 | Out-Null
    }
}

Start-Sleep -Milliseconds 800
Write-Host "  [OK] プロセスの停止処理が完了しました。" -ForegroundColor Green

# =====================================================================
# Step 2: Selective Browser Storage Cleanup (http://127.0.0.1:8080)
# =====================================================================
Write-Host "`n[Step 2/4] ブラウザ内データ (会話履歴 / 設定・Cookie) の処理..." -ForegroundColor Yellow

if (-not $deleteHistory -and -not $deleteSettings) {
    Write-Host "  [KEEP] ブラウザ内の会話履歴・設定・Cookieはすべてそのまま保持します。" -ForegroundColor Green
} else {
    Write-Host "  [CLEAN] ブラウザ内の指定データ (http://127.0.0.1:8080) を消去しています..." -ForegroundColor Cyan
    $jsDeleteHistory = if ($deleteHistory) { "true" } else { "false" }
    $jsDeleteSettings = if ($deleteSettings) { "true" } else { "false" }

    $listener = New-Object System.Net.HttpListener
    $listener.Prefixes.Add("http://127.0.0.1:8080/")
    $listenerStarted = $false

    try {
        $listener.Start()
        $listenerStarted = $true
    } catch {
        Write-Host "  [WARN] ポート 8080 で一時クリーンアップサーバーを起動できませんでした ($($_.Exception.Message))。" -ForegroundColor DarkYellow
    }

    if ($listenerStarted) {
        try {
            $cleanupUrl = "http://127.0.0.1:8080/deinei-uninstall-cleanup"
            Start-Process $cleanupUrl | Out-Null
            Write-Host "  [INFO] ブラウザを開いてデータを消去しています (最大15秒待機)..." -ForegroundColor DarkGray

            $deadline = (Get-Date).AddSeconds(15)
            $doneReceived = $false

            while ((Get-Date) -lt $deadline -and (-not $doneReceived)) {
                $asyncTask = $listener.BeginGetContext($null, $null)
                if ($asyncTask.AsyncWaitHandle.WaitOne(1000)) {
                    $context = $listener.EndGetContext($asyncTask)
                    $reqPath = $context.Request.Url.AbsolutePath
                    $resp = $context.Response

                    if ($reqPath -eq "/deinei-uninstall-done") {
                        $doneReceived = $true
                        $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes("OK")
                        $resp.ContentType = "text/plain; charset=utf-8"
                        $resp.StatusCode = 200
                        $resp.OutputStream.Write($bodyBytes, 0, $bodyBytes.Length)
                        $resp.Close()
                        break
                    } else {
                        if ($deleteHistory -and $deleteSettings) {
                            # 両方削除時は Clear-Site-Data ヘッダも併用してオリジン単位で一括消去
                            $resp.Headers.Add("Clear-Site-Data", '"cache", "cookies", "storage"')
                        }
                        $html = @"
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>Deinei ブラウザデータ クリーンアップ</title>
<style>
  body { background: #0f1419; color: #e6edf3; font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
  .box { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 24px 32px; max-width: 520px; line-height: 1.6; }
  h2 { margin-top: 0; font-size: 18px; color: #58a6ff; }
  .status { font-weight: bold; color: #3fb950; margin-top: 12px; }
</style>
</head>
<body>
<div class="box">
  <h2>泥濘 (Deinei) ブラウザデータの整理</h2>
  <div id="msg">選択されたブラウザ内データを消去しています...</div>
  <div id="detail" class="status"></div>
</div>
<script>
(async function() {
  const delHistory = $jsDeleteHistory;
  const delSettings = $jsDeleteSettings;
  const results = [];

  try {
    if (delSettings) {
      try { localStorage.clear(); } catch (e) {}
      try { sessionStorage.clear(); } catch (e) {}
      try {
        const cookies = document.cookie.split(";");
        for (const c of cookies) {
          const eqPos = c.indexOf("=");
          const name = (eqPos > -1 ? c.substr(0, eqPos) : c).trim();
          if (name) {
            document.cookie = name + "=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/";
          }
        }
      } catch (e) {}
      try {
        if ('serviceWorker' in navigator) {
          const regs = await navigator.serviceWorker.getRegistrations();
          for (const r of regs) { await r.unregister(); }
        }
        if ('caches' in window) {
          const keys = await caches.keys();
          for (const k of keys) { await caches.delete(k); }
        }
      } catch (e) {}
      results.push("WebUIパラメータ・設定・Cookieを消去しました");
    } else {
      results.push("WebUIパラメータ・設定・Cookieは保持しました");
    }

    if (delHistory) {
      await Promise.all([
        new Promise((resolve) => {
          try {
            const req = indexedDB.deleteDatabase("LlamaUi");
            req.onsuccess = () => resolve();
            req.onerror = () => resolve();
            req.onblocked = () => resolve();
            setTimeout(resolve, 1500);
          } catch (e) { resolve(); }
        }),
        new Promise((resolve) => {
          try {
            const req = indexedDB.deleteDatabase("LlamaCppWebui");
            req.onsuccess = () => resolve();
            req.onerror = () => resolve();
            req.onblocked = () => resolve();
            setTimeout(resolve, 1500);
          } catch (e) { resolve(); }
        })
      ]);
      results.push("ブラウザ内の会話履歴 (IndexedDB: LlamaUi) を消去しました");
    } else {
      results.push("ブラウザ内の会話履歴 (IndexedDB) は保持しました");
    }
  } catch (err) {}

  document.getElementById("msg").textContent = "[OK] ブラウザ内データの処理が完了しました。このタブは閉じて構いません。";
  document.getElementById("detail").innerHTML = results.join("<br>");

  try {
    await fetch("/deinei-uninstall-done", { method: "POST" });
  } catch (e) {}

  setTimeout(() => { try { window.close(); } catch (e) {} }, 1200);
})();
</script>
</body>
</html>
"@
                        $htmlBytes = [System.Text.Encoding]::UTF8.GetBytes($html)
                        $resp.ContentType = "text/html; charset=utf-8"
                        $resp.StatusCode = 200
                        $resp.OutputStream.Write($htmlBytes, 0, $htmlBytes.Length)
                        $resp.Close()
                    }
                }
            }

            if ($doneReceived) {
                Write-Host "  [OK] ブラウザ内の指定データの消去が完了しました。" -ForegroundColor Green
            } else {
                Write-Host "  [INFO] ブラウザ応答待機がタイムアウトしました。ファイル削除処理を続行します。" -ForegroundColor DarkYellow
            }
        } catch {
            Write-Host "  [WARN] ブラウザクリーンアップ中に例外が発生しました: $($_.Exception.Message)" -ForegroundColor DarkYellow
        } finally {
            try { $listener.Stop(); $listener.Close() } catch {}
        }
    }
}

# =====================================================================
# Step 3: Remove Unified Caches, Virtual Env, Binaries, Models & Selected Data
# =====================================================================
Write-Host "`n[Step 3/4] キャッシュ・仮想環境・モデル重みを削除しています..." -ForegroundColor Yellow

function Remove-PathSafe {
    param(
        [string]$TargetPath,
        [string]$Label
    )
    if (Test-Path $TargetPath) {
        Write-Host "  [DELETE] $Label を削除中: $TargetPath" -ForegroundColor Cyan
        Remove-Item -Path $TargetPath -Recurse -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path $TargetPath)) {
            Write-Host "  [OK] 削除完了: $Label" -ForegroundColor Green
        } else {
            Write-Host "  [WARN] 一部のファイルがロックされているため残存しました: $TargetPath" -ForegroundColor DarkYellow
        }
    }
}

# 3.1 Unified Cache (.cache & legacy .uv_cache inside project root only)
Remove-PathSafe -TargetPath (Join-Path $rootDir ".cache") -Label "統合キャッシュ (.cache)"
Remove-PathSafe -TargetPath (Join-Path $rootDir ".uv_cache") -Label "旧 uv キャッシュ (.uv_cache)"

# 3.2 Python Virtual Environments & local uv binaries
Remove-PathSafe -TargetPath (Join-Path $rootDir "backend\.venv") -Label "Python 仮想環境 (backend\.venv)"
Remove-PathSafe -TargetPath (Join-Path $rootDir "backend\Irodori-TTS\.venv") -Label "旧 TTS 仮想環境"
foreach ($uvBin in @("uv.exe", "uvw.exe", "uvx.exe")) {
    Remove-PathSafe -TargetPath (Join-Path $rootDir "installer\$uvBin") -Label "ローカル $uvBin"
}

# 3.3 TTS Model Weights
Remove-PathSafe -TargetPath (Join-Path $rootDir "backend\Irodori-TTS\models\model.safetensors") -Label "TTS モデル重み (model.safetensors)"

# 3.4 Sandbox (llama.cpp binaries, CUDA DLLs, GGUF models, and optional Live2D preservation)
if (Test-Path $llamaDir) {
    if ($hasUserAvatars -and (-not $deleteAvatars)) {
        Write-Host "  [KEEP] 導入済みアバターモデル (Live2D / VRM) を保護しながら sandbox 内のバイナリ・LLMモデルを削除しています..." -ForegroundColor Cyan
        # Backup user avatar models temporarily inside rootDir, remove llamaDir, then restore
        $tempAvatarBackup = Join-Path $rootDir "_temp_avatar_preserve"
        if (Test-Path $tempAvatarBackup) { Remove-Item $tempAvatarBackup -Recurse -Force -ErrorAction SilentlyContinue }
        New-Item -ItemType Directory -Path $tempAvatarBackup -Force | Out-Null

        # Preserve models directory and known user model folders under custom_public\deining
        if (Test-Path $deiningPublicDir) {
            $items = Get-ChildItem -Path $deiningPublicDir -Directory -ErrorAction SilentlyContinue
            foreach ($dirItem in $items) {
                Copy-Item -Path $dirItem.FullName -Destination (Join-Path $tempAvatarBackup $dirItem.Name) -Recurse -Force -ErrorAction SilentlyContinue
            }
        }

        Remove-Item -Path $llamaDir -Recurse -Force -ErrorAction SilentlyContinue

        # Restore preserved user avatar model directories
        New-Item -ItemType Directory -Path $deiningPublicDir -Force | Out-Null
        $backedUpDirs = Get-ChildItem -Path $tempAvatarBackup -Directory -ErrorAction SilentlyContinue
        foreach ($bDir in $backedUpDirs) {
            Copy-Item -Path $bDir.FullName -Destination (Join-Path $deiningPublicDir $bDir.Name) -Recurse -Force -ErrorAction SilentlyContinue
        }
        Remove-Item -Path $tempAvatarBackup -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  [OK] LLMモデル・バイナリを削除し、アバターモデルフォルダ (Live2D / VRM) を保持しました。" -ForegroundColor Green
    } else {
        Remove-PathSafe -TargetPath $llamaDir -Label "LLM サンドボックス・GGUFモデル一式"
    }
}

# 3.5 Conversation History & ChromaDB (only if user chose to delete)
if ($deleteHistory) {
    Remove-PathSafe -TargetPath (Join-Path $rootDir "backend\data\chats") -Label "会話履歴JSON (backend\data\chats)"
    Remove-PathSafe -TargetPath (Join-Path $rootDir "backend\data\chroma_db") -Label "長期記憶ベクトルDB (backend\data\chroma_db)"
    Remove-PathSafe -TargetPath (Join-Path $rootDir "backend\data\chroma_db_v2") -Label "長期記憶ベクトルDB v2"
    $logFiles = Get-ChildItem -Path (Join-Path $rootDir "backend\data") -Filter "*.log" -ErrorAction SilentlyContinue
    foreach ($lf in $logFiles) {
        Remove-Item -Path $lf.FullName -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "  [KEEP] PC内の会話履歴 (chats) と長期記憶 (chroma_db) を保持しました。" -ForegroundColor Green
}

# 3.6 Hardware / Startup Profile (only if user chose to delete settings)
if ($deleteSettings) {
    Remove-PathSafe -TargetPath (Join-Path $rootDir "deining_profile.bat") -Label "ハードウェア設定プロファイル (deining_profile.bat)"
} else {
    Write-Host "  [KEEP] ハードウェア設定プロファイル (deining_profile.bat) を保持しました。" -ForegroundColor Green
}

# =====================================================================
# Step 4: Remove Desktop Shortcut & Generated Launcher Scripts
# =====================================================================
Write-Host "`n[Step 4/4] ショートカットと自動生成スクリプトを削除しています..." -ForegroundColor Yellow

try {
    $desktopPath = [System.Environment]::GetFolderPath('Desktop')
    $shortcutPath = Join-Path $desktopPath "泥濘 (Deinei) を起動.lnk"
    if (Test-Path $shortcutPath) {
        $shouldDeleteShortcut = $true
        try {
            $wshShell = New-Object -ComObject WScript.Shell
            $sc = $wshShell.CreateShortcut($shortcutPath)
            if ($sc.TargetPath) {
                $scTargetLower = $sc.TargetPath.ToLower()
                if (-not $scTargetLower.StartsWith($rootWithSlash)) {
                    $shouldDeleteShortcut = $false
                }
            }
        } catch {}

        if ($shouldDeleteShortcut) {
            Remove-Item -Path $shortcutPath -Force -ErrorAction SilentlyContinue
            Write-Host "  [OK] デスクトップショートカットを削除しました: $shortcutPath" -ForegroundColor Green
        } else {
            Write-Host "  [KEEP] デスクトップショートカットは別の泥濘フォルダを参照しているため保持しました。" -ForegroundColor Green
        }
    }
} catch {}

$subScriptsDir = Join-Path $rootDir "scripts"
if (Test-Path $subScriptsDir) {
    Remove-Item -Path $subScriptsDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "  [OK] 補助起動スクリプトフォルダを削除しました: scripts\" -ForegroundColor Green
}

$generatedBats = @(
    "start_ai_servers.bat",
    "start_ai_servers_lan.bat",
    "start_ai_servers_tailscale.bat",
    "deinei_doctor.bat"
)
foreach ($batName in $generatedBats) {
    $batPath = Join-Path $rootDir $batName
    if (Test-Path $batPath) {
        Remove-Item -Path $batPath -Force -ErrorAction SilentlyContinue
        Write-Host "  [OK] 生成スクリプトを削除しました: $batName" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host " [OK] アンインストール処理が完了しました！" -ForegroundColor Green
Write-Host "======================================================================" -ForegroundColor Cyan
if ((-not $deleteHistory) -or (-not $deleteSettings) -or ($hasUserLive2d -and (-not $deleteLive2d))) {
    Write-Host "  [INFO] 保持を選択したデータはそのまま残されています。" -ForegroundColor Cyan
    Write-Host "         今後 install.bat を再実行すると、残した履歴や設定を引き継いで再構築できます。"
} else {
    Write-Host "  [INFO] フォルダ自体も不要な場合は、このウィンドウを閉じた後に" -ForegroundColor Cyan
    Write-Host "         フォルダ ($rootDir) をゴミ箱へ移動して削除できます。"
}
Write-Host ""
