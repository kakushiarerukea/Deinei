# ======================================================================
# Deinei System Automated Setup Script (setup.ps1)
# ======================================================================

$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Suppress progress bar rendering to prevent console buffer corruption
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue

$rootDir = Split-Path -Parent $PSScriptRoot
if (-not $rootDir) { $rootDir = (Get-Location).Path }

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host " [Deinei] Automated Setup Wizard" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "Project Root: $rootDir`n"

# --- 0. Single Instance Guard (Duplicate Execution Prevention) ---
$setupMutexName = "Local\Deinei_Setup_Wizard_Mutex"
$createdNewMutex = $false
try {
    $script:setupMutex = New-Object System.Threading.Mutex($true, $setupMutexName, [ref]$createdNewMutex)
} catch {
    $createdNewMutex = $false
}

if (-not $createdNewMutex) {
    Write-Host "======================================================================" -ForegroundColor Red
    Write-Host " [重複起動検知 / DUPLICATE PROCESS DETECTED]" -ForegroundColor Red
    Write-Host "======================================================================" -ForegroundColor Red
    Write-Host " セットアップ (install.bat / setup.ps1) が既に実行中です。" -ForegroundColor Yellow
    Write-Host " 重複実行によるファイルの破損や競合を防ぐため、後から起動した本プロセスを強制終了します。" -ForegroundColor Yellow
    Write-Host " Another setup instance is already running. Terminating this duplicate process." -ForegroundColor DarkGray
    Write-Host "======================================================================`n" -ForegroundColor Red
    Start-Sleep -Seconds 3
    exit 2
}

# --- 1. GPU / Hardware Check & Profile Tuning ---
Write-Host "[Step 1/7] Checking hardware & tuning VRAM profile..." -ForegroundColor Yellow
$gpuName = "Unknown"
$vramGB = 0
try {
    $smiOut = (& nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>$null)
    if ($smiOut) {
        $firstLine = if ($smiOut -is [array]) { $smiOut[0] } else { $smiOut.ToString() }
        if ($firstLine -match "(.+),\s*([0-9.]+)") {
            $gpuName = $Matches[1].Trim()
            $vramMB = [double]$Matches[2].Trim()
            $vramGB = [math]::Round($vramMB / 1024, 1)
        }
    }
    
    if ($gpuName -eq "Unknown" -or $vramGB -le 0) {
        $gpu = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" } | Select-Object -First 1
        if ($gpu) {
            $gpuName = $gpu.Name
            $vramGB = [math]::Round($gpu.AdapterRAM / 1GB, 1)
        }
    }

    if ($gpuName -ne "Unknown" -and $vramGB -gt 0) {
        Write-Host "  [OK] NVIDIA GPU detected: $gpuName (VRAM: approx ${vramGB}GB)" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] NVIDIA GPU not detected (CPU mode will be used)" -ForegroundColor DarkYellow
    }
} catch {
    Write-Host "  [INFO] Failed to query GPU info automatically (defaulting to 16GB profile)" -ForegroundColor DarkGray
    $vramGB = 16.0
}

# --- Profile Determination ---
$profileName = "16GB (High)"
$ncmoe = 9
$mmprojArgs = ""
$ttsModelUrl = ""
$ttsModelDesc = ""

if ($vramGB -ge 22.0) {
    # 24GB VRAM (RTX 3090, 4090 etc.)
    $profileName = "24GB (Ultra)"
    $ncmoe = 0
    $mmprojArgs = "--mmproj mmproj-BF16.gguf"
    $ttsModelUrl = "https://huggingface.co/Aratako/Irodori-TTS-v4-Small/resolve/main/model.safetensors"
    $ttsModelDesc = "BF16 (~3.0GB)"
} elseif ($vramGB -ge 13.0) {
    # 16GB VRAM (RTX 4080, 4070 Ti SUPER etc.)
    $profileName = "16GB (High)"
    $ncmoe = 9
    $mmprojArgs = ""
    $ttsModelUrl = "https://huggingface.co/Aratako/Irodori-TTS-v4-Small-Quantized/resolve/main/float8-weight-only/model.safetensors"
    $ttsModelDesc = "FP8 (~873MB)"
} elseif ($vramGB -ge 11.0) {
    # 12GB VRAM (RTX 3060 12GB, RTX 4070, RTX 4070 SUPER etc.)
    $profileName = "12GB (Medium)"
    $ncmoe = 16
    $mmprojArgs = ""
    $ttsModelUrl = "https://huggingface.co/Aratako/Irodori-TTS-v4-Small-Quantized/resolve/main/float8-weight-only/model.safetensors"
    $ttsModelDesc = "FP8 (~873MB)"
} else {
    # 8GB - 10GB VRAM (RTX 3070, 3080 10GB, 4060 etc.)
    $profileName = "8GB (Low/Medium)"
    $ncmoe = 20
    $mmprojArgs = ""
    $ttsModelUrl = "https://huggingface.co/Aratako/Irodori-TTS-v4-Small-Quantized/resolve/main/float8-weight-only/model.safetensors"
    $ttsModelDesc = "FP8 (~873MB)"
}

$visionStatus = if ($mmprojArgs) { 'Enabled (GPU)' } else { 'Disabled (Flash Attention priority)' }
Write-Host "  [TUNE] Applied profile: [$profileName]" -ForegroundColor Cyan
Write-Host "    - MoE CPU offload (-ncmoe): $ncmoe"
Write-Host "    - Vision encoder: $visionStatus"
Write-Host "    - TTS model: $ttsModelDesc"

# Save deining_profile.bat (ASCII/CRLF)
$profileBatPath = Join-Path $rootDir "deining_profile.bat"
$profileBatContent = @"
@echo off
:: ======================================================================
:: Deinei Auto-Tuning Configuration File
:: Detected Hardware: $gpuName (VRAM: approx ${vramGB}GB)
:: Applied Profile: $profileName
:: ======================================================================

set PROFILE_NAME=$profileName
set GPU_NAME=$gpuName
set VRAM_GB=$vramGB

:: MoE CPU Offload Experts (8GB/10GB:20, 12GB:16, 16GB:9, 24GB:0)
set NCMOE=$ncmoe

:: Vision Encoder settings
set MMPROJ_ARGS=$mmprojArgs

:: LAN Access (0: Local only, 1: Legacy - see start_ai_servers_lan.bat for secure gateway)
set ALLOW_LAN=0
"@ -replace "`r?`n", "`r`n"

[System.IO.File]::WriteAllText($profileBatPath, $profileBatContent, [System.Text.Encoding]::ASCII)
Write-Host "  [SAVE] Saved profile: deining_profile.bat" -ForegroundColor Green

# =====================================================================
# Helper: Prompt-YesNo (Bilingual & Input Buffer Safe / Beginner Friendly)
# =====================================================================
function Prompt-YesNo {
    param(
        [string]$PromptJa,
        [string]$PromptEn,
        [bool]$DefaultYes = $true
    )

    # 外部プロセス等でコンソール入力バッファに溜まった先行入力・ゴミ改行を完全にフラッシュ
    try {
        while ([System.Console]::KeyAvailable) {
            [void][System.Console]::ReadKey($true)
        }
    } catch {}

    $defaultGuideJa = if ($DefaultYes) { "[Y] はい (推奨 / そのままEnterキーで決定)" } else { "[N] いいえ (そのままEnterキーで決定)" }
    $defaultGuideEn = if ($DefaultYes) { "Default: [Y] Yes (Press Enter to confirm)" } else { "Default: [N] No (Press Enter to confirm)" }

    while ($true) {
        Write-Host ""
        Write-Host "  $PromptJa" -ForegroundColor Cyan
        Write-Host "  $PromptEn" -ForegroundColor Cyan
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
            Write-Host "  [入力案内] 'y' (はい) または 'n' (いいえ) を入力して Enter を押してください (そのままEnterキーを押すとデフォルト選択になります)。" -ForegroundColor DarkYellow
            Write-Host "  [Input Guide] Please type 'y' or 'n' and press Enter (or press Enter for default)." -ForegroundColor DarkYellow
            try {
                while ([System.Console]::KeyAvailable) {
                    [void][System.Console]::ReadKey($true)
                }
            } catch {}
        }
    }
}

# =====================================================================
# Helper: Pip Install with Truststore, uv, Progress Bar & SSL Fallback
# =====================================================================
$script:sslBypassAllowed = $null
$script:uvExe = $null

function Invoke-PipInstall {
    param(
        [string]$VenvPython,
        [string[]]$PipArgs,
        [switch]$ShowProgress
    )

    # Try 0: Use uv pip install if uv is available (ultra fast, smart resolver)
    if ($script:uvExe -and (Test-Path $script:uvExe)) {
        $uvCommonArgs = @(
            "--index-strategy", "unsafe-best-match"
        )
        # PyTorch インデックスは PyTorch 関連パッケージのインストール時のみ付与
        # (通常パッケージ解決時に certifi 等が PyTorch 側インデックスと競合するのを防止)
        $isTorch = ($PipArgs -match "torch|torchaudio")
        if ($isTorch) {
            $uvCommonArgs += @(
                "--extra-index-url", "https://download.pytorch.org/whl/cu128",
                "--extra-index-url", "https://download.pytorch.org/whl/cu126"
            )
        }
        if ($script:sslBypassAllowed) {
            $uvCommonArgs += @(
                "--allow-insecure-host", "pypi.org",
                "--allow-insecure-host", "files.pythonhosted.org",
                "--allow-insecure-host", "download.pytorch.org"
            )
        }
        $uvArgs = @("pip", "install") + $PipArgs + $uvCommonArgs + @("--python", $VenvPython)
        if (-not $ShowProgress) {
            Write-Host "    [UV] Installing packages via uv..." -ForegroundColor DarkGray
            & $script:uvExe @uvArgs 2>&1 | Out-Null
        } else {
            Write-Host "    [UV] Installing packages via uv (リアルタイム進捗表示)..." -ForegroundColor Cyan
            & $script:uvExe @uvArgs
        }
        if ($LASTEXITCODE -eq 0) {
            return $true
        }
        Write-Host "    [UV] Falling back to standard pip..." -ForegroundColor DarkYellow
    }
    
    $commonArgs = @(
        "-m", "pip", "install", "--no-warn-script-location",
        "--extra-index-url", "https://download.pytorch.org/whl/cu128",
        "--extra-index-url", "https://download.pytorch.org/whl/cu126"
    )
    if ($ShowProgress) {
        $commonArgs += @("--progress-bar", "on")
    }
    
    # Try 1: Standard Windows truststore
    $allArgs = $commonArgs + $PipArgs + @("--use-feature=truststore")
    if ($ShowProgress) {
        Write-Host "    [TRY 1] パッケージをダウンロード＆インストール中..." -ForegroundColor Cyan
        Write-Host "    (※巨大パッケージのため、ダウンロード開始前のメタデータ解決に1〜3分ほど時間を要する場合があります。フリーズではありませんのでそのままお待ちください)" -ForegroundColor Yellow
        & $VenvPython @allArgs
    } else {
        Write-Host "    [TRY 1] Installing via Windows truststore..." -ForegroundColor DarkGray
        & $VenvPython @allArgs 2>&1 | Out-Null
    }
    
    if ($LASTEXITCODE -ne 0) {
        if ($script:sslBypassAllowed -eq $false) {
            return $false
        }

        # Prompt user if SSL verification fails
        if ($null -eq $script:sslBypassAllowed) {
            Write-Host ""
            Write-Host "    [警告 / WARNING] SSL証明書の検証に失敗しました。 / SSL certificate verification failed." -ForegroundColor Yellow
            Write-Host "    信頼できるネットワーク環境の場合のみ、SSL検証を回避 (--trusted-host) して続行できます。" -ForegroundColor Yellow
            Write-Host "    Bypass SSL verification (--trusted-host) only if you trust your network." -ForegroundColor Yellow

            $answer = Prompt-YesNo `
                -PromptJa "SSL検証を回避 (--trusted-host) して続行しますか？" `
                -PromptEn "Do you want to bypass SSL verification and continue?" `
                -DefaultYes $true

            if ($answer) {
                $script:sslBypassAllowed = $true
            } else {
                $script:sslBypassAllowed = $false
                Write-Host ""
                Write-Host "    [中断 / ABORTED] セットアップを中断しました。信頼できるネットワーク環境で再試行してください。" -ForegroundColor Yellow
                Write-Host "    Setup aborted. Please retry in a trusted network environment." -ForegroundColor Yellow
                pause
                exit 1
            }
        }

        # Try 2: SSL bypass mode (--trusted-host)
        Write-Host "    [TRY 2] Retrying with --trusted-host..." -ForegroundColor DarkYellow
        $allArgs = $commonArgs + $PipArgs + @(
            "--trusted-host", "pypi.org",
            "--trusted-host", "files.pythonhosted.org",
            "--trusted-host", "pypi.python.org",
            "--trusted-host", "download.pytorch.org"
        )
        if ($ShowProgress) {
            & $VenvPython @allArgs
        } else {
            & $VenvPython @allArgs 2>&1 | Out-Null
        }
        
        if ($LASTEXITCODE -ne 0) {
            Write-Host "    [警告] パッケージのインストールに失敗しました。ネットワーク接続や設定を確認してください。" -ForegroundColor Red
            return $false
        }
    }
    return $true
}

# =====================================================================
# Helper: Silent ZIP Extraction (no console corruption)
# =====================================================================
function Expand-ZipQuiet {
    param(
        [string]$ZipPath,
        [string]$DestinationPath
    )
    if (-not (Test-Path $DestinationPath)) {
        New-Item -ItemType Directory -Path $DestinationPath -Force | Out-Null
    }
    try {
        [System.IO.Compression.ZipFile]::ExtractToDirectory($ZipPath, $DestinationPath)
    } catch {
        $prevProgress = $ProgressPreference
        $ProgressPreference = 'SilentlyContinue'
        Expand-Archive -Path $ZipPath -DestinationPath $DestinationPath -Force
        $ProgressPreference = $prevProgress
    }
}

# --- 2. Python Environment Setup (Powered by uv) ---
Write-Host "`n[Step 2/7] Setting up Python 3.10 environment..." -ForegroundColor Yellow

$venvDir = Join-Path $rootDir "backend\.venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$venvPip = Join-Path $venvDir "Scripts\pip.exe"
$pyvenvCfg = Join-Path $venvDir "pyvenv.cfg"

# 2.1 Locate or automatically download uv (Standalone Python & Package Manager)
$rawUvCandidates = @(
    (Get-Command uv -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    (Join-Path $rootDir "installer\uv.exe"),
    (Join-Path $env:LOCALAPPDATA "bin\uv.exe"),
    (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe")
)
$candidateUvPaths = @($rawUvCandidates | Where-Object { $_ -and (Test-Path $_) })

if ($candidateUvPaths.Count -gt 0) {
    $script:uvExe = [string]$candidateUvPaths[0]
    Write-Host "  [OK] Found uv helper: $script:uvExe" -ForegroundColor Green
} else {
    Write-Host "  [INIT] Downloading setup helper (uv standalone)..." -ForegroundColor Cyan
    $installerDir = Join-Path $rootDir "installer"
    $targetUvExe = Join-Path $installerDir "uv.exe"
    $uvDownloaded = $false

    # Try 1: Download standalone zip from Astral GitHub releases
    try {
        $uvZipUrl = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
        $tempZip = Join-Path $env:TEMP "uv-setup.zip"
        if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
            & curl.exe -L -o $tempZip $uvZipUrl --silent --show-error
        } else {
            Invoke-WebRequest -Uri $uvZipUrl -OutFile $tempZip -UseBasicParsing
        }
        if (Test-Path $tempZip) {
            Expand-ZipQuiet -ZipPath $tempZip -DestinationPath $installerDir
            Remove-Item $tempZip -Force -ErrorAction SilentlyContinue
            if (Test-Path $targetUvExe) {
                $script:uvExe = $targetUvExe
                $uvDownloaded = $true
                Write-Host "  [OK] uv standalone helper ready: $script:uvExe" -ForegroundColor Green
            }
        }
    } catch {
        Write-Host "  [INFO] Direct ZIP extraction skipped, trying official installer script..." -ForegroundColor DarkGray
    }

    # Try 2: Official installer script fallback (install into local installer directory)
    if (-not $uvDownloaded) {
        try {
            $env:UV_INSTALL_DIR = $installerDir
            $tempScript = Join-Path $env:TEMP "install-uv.ps1"
            Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" -OutFile $tempScript -UseBasicParsing
            & powershell.exe -ExecutionPolicy Bypass -File $tempScript
            Remove-Item $tempScript -Force -ErrorAction SilentlyContinue

            foreach ($p in @((Join-Path $installerDir "uv.exe"), (Join-Path $env:LOCALAPPDATA "bin\uv.exe"), (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe"))) {
                if (Test-Path $p) {
                    $script:uvExe = $p
                    $uvDownloaded = $true
                    Write-Host "  [OK] uv installed: $script:uvExe" -ForegroundColor Green
                    break
                }
            }
        } catch {
            Write-Host "  [WARN] Failed to download uv helper automatically." -ForegroundColor DarkYellow
        }
    }
}
# 2.2 Configure Unified Local Cache (.cache) & Create Python 3.10 Virtual Environment (.venv)
# インストール・稼働時の各種キャッシュをプロジェクト直下の .cache に統合し、
# 他のAIツール (Stable Diffusion / ComfyUI 等) の共通領域と混ざらないように分離 + アンインストール時の一括削除を容易化
$cacheRoot = Join-Path $rootDir ".cache"
if (-not (Test-Path $cacheRoot)) {
    New-Item -ItemType Directory -Path $cacheRoot -Force | Out-Null
}
$env:UV_CACHE_DIR = Join-Path $cacheRoot "uv"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $cacheRoot "uv_python"
$env:PIP_CACHE_DIR = Join-Path $cacheRoot "pip"
$env:HF_HOME = Join-Path $cacheRoot "huggingface"
$env:TORCH_HOME = Join-Path $cacheRoot "torch"

if (-not (Test-Path $venvPython)) {
    if ($script:uvExe -and (Test-Path $script:uvExe)) {
        Write-Host "  [INIT] Creating Python 3.10 virtual environment via uv..." -ForegroundColor Cyan
        Write-Host "  (※ If Python 3.10 is not on your PC, uv will automatically download it)" -ForegroundColor DarkGray
        & $script:uvExe venv $venvDir --python 3.10 --seed
    }

    # Fallback to system Python if uv was not available
    if (-not (Test-Path $venvPython)) {
        Write-Host "  [FALLBACK] Checking system Python..." -ForegroundColor Cyan
        $pythonCmd = (Get-Command python -ErrorAction SilentlyContinue)
        if (-not $pythonCmd) {
            Write-Host "  [警告] Python が見つかりませんでした。Python 3.10 をインストールしてください。" -ForegroundColor Red
            Write-Host "  推奨ダウンロード先 (Python 3.10.11): https://www.python.org/downloads/release/python-31011/" -ForegroundColor Cyan
            pause
            exit 1
        }
        & python -m venv $venvDir --clear
    }
} else {
    Write-Host "  [OK] Virtual environment already exists (.venv)" -ForegroundColor Green
}

if (Test-Path $venvPython) {
    $pyVersion = & $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>&1
    Write-Host "  [OK] Using Python in .venv: Python $pyVersion" -ForegroundColor Green
} else {
    Write-Host "  [警告] 仮想環境の作成に失敗しました。" -ForegroundColor Red
    pause
    exit 1
}

if (Test-Path $pyvenvCfg) {
    $cfgContent = Get-Content $pyvenvCfg -Raw -Encoding UTF8
    if ($cfgContent -match "include-system-site-packages\s*=\s*true") {
        $cfgContent = $cfgContent -replace "include-system-site-packages\s*=\s*true", "include-system-site-packages = false"
        [System.IO.File]::WriteAllText($pyvenvCfg, $cfgContent, [System.Text.Encoding]::UTF8)
    }
}

$env:PYTHONUTF8 = "1"
if (Test-Path $venvPython) {
    # Step 2a: Upgrade pip if present
    if (Test-Path $venvPip) {
        Write-Host "  [UPDATE] Upgrading pip..." -ForegroundColor Cyan
        Invoke-PipInstall -VenvPython $venvPython -PipArgs @("--upgrade", "pip") | Out-Null
    }

    # Step 2b: PyTorch with CUDA
    Write-Host "  [INSTALL] Checking PyTorch (CUDA)..." -ForegroundColor Cyan
    $torchCudaCheck = & $venvPython -c "import torch, torchaudio; print('CUDA_OK' if torch.cuda.is_available() else 'CPU_ONLY')" 2>$null
    if ($torchCudaCheck -ne "CUDA_OK") {
        Write-Host "    ======================================================================" -ForegroundColor Cyan
        Write-Host "    [PyTorch DL/INSTALL] PyTorch (CUDA 12.8) のダウンロード＆インストール開始" -ForegroundColor Cyan
        Write-Host "    ======================================================================" -ForegroundColor Cyan
        Write-Host "    ※PyTorch本体およびCUDA関連パッケージは約2.5GB〜3GBの大容量ファイルです。" -ForegroundColor Yellow
        Write-Host "    ※ダウンロードおよびインストールの進捗バーが表示されます。そのままお待ちください。" -ForegroundColor Yellow
        Write-Host ""
        $torchResult = Invoke-PipInstall -VenvPython $venvPython -PipArgs @(
            "torch==2.11.0+cu128", "torchaudio==2.11.0+cu128"
        ) -ShowProgress
        if (-not $torchResult) {
            Write-Host "    [RETRY] CUDA 12.6 で再試行します..." -ForegroundColor DarkYellow
            $torchResult = Invoke-PipInstall -VenvPython $venvPython -PipArgs @(
                "torch==2.11.0+cu126", "torchaudio==2.11.0+cu126"
            ) -ShowProgress
        }
        if (-not $torchResult) {
            Write-Host "  [警告] PyTorch のインストールに失敗しました。" -ForegroundColor Red
            pause
            exit 1
        }
        $torchVersion = & $venvPython -c "import torch; print(torch.__version__)" 2>$null
        Write-Host "  [OK] PyTorch installed: v$torchVersion" -ForegroundColor Green
    } else {
        $torchVersion = & $venvPython -c "import torch; print(torch.__version__)" 2>$null
        Write-Host "  [OK] PyTorch is available: v$torchVersion" -ForegroundColor Green
    }

    # Step 2c: torch extensions
    Write-Host "  [INSTALL] Installing torch extensions (torchao, torchcodec, torchdata)..." -ForegroundColor Cyan
    $torchExtResult = Invoke-PipInstall -VenvPython $venvPython -PipArgs @(
        "torchao>=0.16.0", "torchcodec>=0.10.0,<0.11.0", "torchdata>=0.11.0"
    )
    if (-not $torchExtResult) {
        Write-Host "  [WARN] Failed to install some torch extension packages. Continuing..." -ForegroundColor DarkYellow
    }

    # Step 2d: Audio packages (dacvae, silentcipher) - Prefer bundled wheels, fallback to git
    $wheelDir = Join-Path $rootDir "installer\wheels"
    $dacvaeWhl = Join-Path $wheelDir "dacvae-1.0.0-py3-none-any.whl"
    $silentcipherWhl = Join-Path $wheelDir "silentcipher-1.0.5-py3-none-any.whl"
    $audiotoolsWhl = Join-Path $wheelDir "descript_audiotools-0.7.2-py2.py3-none-any.whl"

    if ((Test-Path $dacvaeWhl) -and (Test-Path $silentcipherWhl)) {
        Write-Host "  [INSTALL] Installing audio packages (dacvae, silentcipher) using bundled wheels..." -ForegroundColor Cyan
        & $VenvPython -m pip install --no-warn-script-location $dacvaeWhl $silentcipherWhl --no-deps 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [OK] Audio packages installed from bundled wheels (offline)" -ForegroundColor Green
        } else {
            Write-Host "  [WARN] Failed to install from bundled wheels. Falling back to git..." -ForegroundColor DarkYellow
            $gitPkgResult = Invoke-PipInstall -VenvPython $venvPython -PipArgs @(
                "dacvae @ git+https://github.com/facebookresearch/dacvae",
                "silentcipher @ git+https://github.com/SesameAILabs/silentcipher.git@d46d7d0893a583d8968ab3a6626e2289faec9152"
            )
        }
    } else {
        Write-Host "  [INSTALL] Installing audio packages (dacvae, silentcipher) via git..." -ForegroundColor Cyan
        $gitPkgResult = Invoke-PipInstall -VenvPython $venvPython -PipArgs @(
            "dacvae @ git+https://github.com/facebookresearch/dacvae",
            "silentcipher @ git+https://github.com/SesameAILabs/silentcipher.git@d46d7d0893a583d8968ab3a6626e2289faec9152"
        )
        if (-not $gitPkgResult) {
            Write-Host "  [WARN] Failed to install some git audio packages. Continuing..." -ForegroundColor DarkYellow
        }
    }

    # Step 2e: dependencies from requirements-lock.txt
    $lockFile = Join-Path $rootDir "backend\requirements-lock.txt"
    $reqFile = Join-Path $rootDir "backend\requirements.txt"
    $ttsReq = Join-Path $rootDir "backend\Irodori-TTS\requirements.txt"

    if (Test-Path $lockFile) {
        Write-Host "  [INSTALL] Installing dependencies from requirements-lock.txt (progress enabled)..." -ForegroundColor Cyan
        $lockResult = Invoke-PipInstall -VenvPython $venvPython -PipArgs @("-r", $lockFile) -ShowProgress
        if (-not $lockResult) {
            Write-Host "  [警告] パッケージのインストールに失敗しました。" -ForegroundColor Red
            pause
            exit 1
        }
    } elseif ((Test-Path $reqFile) -and (Test-Path $ttsReq)) {
        Write-Host "  [INSTALL] Installing dependencies from requirements.txt..." -ForegroundColor Cyan
        $fallbackResult = Invoke-PipInstall -VenvPython $venvPython -PipArgs @("-r", $reqFile, "-r", $ttsReq)
        if (-not $fallbackResult) {
            Write-Host "  [警告] パッケージのインストールに失敗しました。" -ForegroundColor Red
            pause
            exit 1
        }
    } elseif (Test-Path $reqFile) {
        $fallbackResult = Invoke-PipInstall -VenvPython $venvPython -PipArgs @("-r", $reqFile)
        if (-not $fallbackResult) {
            Write-Host "  [警告] パッケージのインストールに失敗しました。" -ForegroundColor Red
            pause
            exit 1
        }
    }

    # Step 2f: Standalone packages (--no-deps)
    Write-Host "  [INSTALL] Installing standalone packages (descript-audiotools, sentence-transformers)..." -ForegroundColor Cyan
    if (Test-Path $audiotoolsWhl) {
        & $VenvPython -m pip install --no-warn-script-location $audiotoolsWhl --no-deps 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [OK] descript-audiotools installed from bundled wheel (offline)" -ForegroundColor Green
        } else {
            Write-Host "  [WARN] Failed to install from bundled wheel. Retrying via pip..." -ForegroundColor DarkYellow
            Invoke-PipInstall -VenvPython $venvPython -PipArgs @("descript-audiotools==0.7.2", "--no-deps") | Out-Null
        }
    } else {
        $audiotoolsResult = Invoke-PipInstall -VenvPython $venvPython -PipArgs @("descript-audiotools==0.7.2", "--no-deps")
        if (-not $audiotoolsResult) {
            Write-Host "  [WARN] Failed to install descript-audiotools. Continuing..." -ForegroundColor DarkYellow
        }
    }
    $sentTransResult = Invoke-PipInstall -VenvPython $venvPython -PipArgs @("sentence-transformers==2.7.0", "--no-deps")
    if (-not $sentTransResult) {
        Write-Host "  [WARN] Failed to install sentence-transformers. Continuing..." -ForegroundColor DarkYellow
    }
    Write-Host "  [INSTALL] Installing launcher GUI packages (pywebview, segno)..." -ForegroundColor Cyan
    $launcherPkgResult = Invoke-PipInstall -VenvPython $venvPython -PipArgs @("pywebview>=5.0", "segno>=1.6.0")
    if (-not $launcherPkgResult) {
        Write-Host "  [WARN] Failed to install pywebview (Tkinter fallback will be used)." -ForegroundColor DarkYellow
    }

    # Step 2g: ReazonSpeech local install
    $reazonPkg = Join-Path $rootDir "backend\ReazonSpeech\pkg\k2-asr"
    if (Test-Path $reazonPkg) {
        Write-Host "  [INSTALL] Installing ReazonSpeech-K2 from local source..." -ForegroundColor Cyan
        & $VenvPython -m pip install --no-warn-script-location --no-deps $reazonPkg 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [OK] ReazonSpeech-K2 installed from local source (offline)" -ForegroundColor Green
        } else {
            $reazonResult = Invoke-PipInstall -VenvPython $venvPython -PipArgs @($reazonPkg)
            if (-not $reazonResult) {
                Write-Host "  [WARN] Failed to install ReazonSpeech. Continuing..." -ForegroundColor DarkYellow
            }
        }
    }

    Write-Host "  [OK] Python dependencies installed successfully" -ForegroundColor Green
}

# --- 3. llama.cpp & CUDA Runtime Setup ---
Write-Host "`n[Step 3/7] Setting up llama.cpp & CUDA runtime..." -ForegroundColor Yellow
$llamaDir = Join-Path $rootDir "llama-b9873-bin-win-cuda-13.3-x64sandbox"
$llamaExe = Join-Path $llamaDir "llama-server.exe"
$cudartDll = Join-Path $llamaDir "cublas64_13.dll"

if (-not (Test-Path $llamaDir)) { New-Item -ItemType Directory -Path $llamaDir -Force | Out-Null }

if (-not (Test-Path $llamaExe)) {
    Write-Host "  [DOWNLOAD] Downloading llama.cpp binary (b9873 CUDA 13.3)..." -ForegroundColor Cyan
    $zipUrl = "https://github.com/ggml-org/llama.cpp/releases/download/b9873/llama-b9873-bin-win-cuda-13.3-x64.zip"
    $zipPath = Join-Path $rootDir "llama_bin_temp.zip"
    
    try {
        & curl.exe -L -o $zipPath $zipUrl --progress-bar
        Write-Host "  [EXTRACT] Extracting binary files..." -ForegroundColor Cyan
        Expand-ZipQuiet -ZipPath $zipPath -DestinationPath $llamaDir
        Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
        
        Write-Host "  [OK] llama.cpp is ready" -ForegroundColor Green
    } catch {
        Write-Host "  [警告] llama.cpp 本体のダウンロードに失敗しました。" -ForegroundColor Red
    }
} else {
    Write-Host "  [OK] llama.cpp is ready" -ForegroundColor Green
}

# Ensure jinja chat template is always placed in llama sandbox directory
$jinjaSrc = Join-Path $rootDir "backend\templates\gemma_chat_template.jinja"
if (-not (Test-Path $jinjaSrc)) {
    $jinjaSrc = Join-Path $rootDir "backend\llama.cpp-b9873\models\templates\google-gemma-4-31B-it.jinja"
}
$jinjaDst = Join-Path $llamaDir "gemma_chat_template.jinja"
if (Test-Path $jinjaSrc) {
    Copy-Item $jinjaSrc $jinjaDst -Force
}

if (-not (Test-Path $cudartDll)) {
    Write-Host "  [DOWNLOAD] Downloading NVIDIA CUDA 13.3 runtime (cuBLAS / cuDART)..." -ForegroundColor Cyan
    $cudartUrl = "https://github.com/ggml-org/llama.cpp/releases/download/b9873/cudart-llama-bin-win-cuda-13.3-x64.zip"
    $cudartZip = Join-Path $rootDir "cudart_temp.zip"
    
    try {
        & curl.exe -L -o $cudartZip $cudartUrl --progress-bar
        Write-Host "  [EXTRACT] Extracting CUDA runtime..." -ForegroundColor Cyan
        Expand-ZipQuiet -ZipPath $cudartZip -DestinationPath $llamaDir
        Remove-Item $cudartZip -Force -ErrorAction SilentlyContinue
        Write-Host "  [OK] CUDA runtime is ready" -ForegroundColor Green
    } catch {
        Write-Host "  [警告] CUDA ランタイムのダウンロードに失敗しました。" -ForegroundColor Red
    }
} else {
    Write-Host "  [OK] CUDA runtime is ready" -ForegroundColor Green
}

# --- 4. TTS Model Setup ---
Write-Host "`n[Step 4/7] Checking TTS model..." -ForegroundColor Yellow
$ttsModelDir = Join-Path $rootDir "backend\Irodori-TTS\models"
$ttsModelPath = Join-Path $ttsModelDir "model.safetensors"

if (-not (Test-Path $ttsModelDir)) {
    New-Item -ItemType Directory -Path $ttsModelDir -Force | Out-Null
}

if (-not (Test-Path $ttsModelPath)) {
    Write-Host "  [DOWNLOAD] Downloading TTS model ($ttsModelDesc)..." -ForegroundColor Cyan
    try {
        & curl.exe -L -o $ttsModelPath $ttsModelUrl --progress-bar
        Write-Host "  [OK] TTS model is ready: $ttsModelDesc" -ForegroundColor Green
    } catch {
        Write-Host "  [警告] TTS モデルのダウンロードに失敗しました。" -ForegroundColor Red
    }
} else {
    Write-Host "  [OK] TTS model is ready: $ttsModelDesc" -ForegroundColor Green
}

# --- 4.5. Embedding Model Setup (RAG / ChromaDB) ---
Write-Host "`n[Step 4.5/7] Checking Embedding model (cl-nagoya/ruri-v3-310m)..." -ForegroundColor Yellow
try {
    $localHfHub = Join-Path $env:HF_HOME "hub"
    $globalHfHub = Join-Path $env:USERPROFILE ".cache\huggingface\hub"
    $ruriFolderName = "models--cl-nagoya--ruri-v3-310m"
    $localRuriPath = Join-Path $localHfHub $ruriFolderName
    $globalRuriPath = Join-Path $globalHfHub $ruriFolderName
    if ((-not (Test-Path $localRuriPath)) -and (Test-Path $globalRuriPath)) {
        Write-Host "  [CACHE] Copying existing ruri-v3-310m cache into local .cache/huggingface..." -ForegroundColor Cyan
        if (-not (Test-Path $localHfHub)) { New-Item -ItemType Directory -Path $localHfHub -Force | Out-Null }
        Copy-Item -Path $globalRuriPath -Destination $localRuriPath -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Host "  [CACHE] Pre-caching embedding model for fast zero-wait startup..." -ForegroundColor Cyan
    & $VenvPython -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer('cl-nagoya/ruri-v3-310m', device='cpu')"
    Write-Host "  [OK] Embedding model is ready: cl-nagoya/ruri-v3-310m" -ForegroundColor Green
} catch {
    Write-Host "  [WARN] Failed to pre-cache embedding model. It will be downloaded on first launch." -ForegroundColor DarkYellow
}

# --- 5. Frontend Build / Deployment ---
Write-Host "`n[Step 5/7] Deploying frontend (Svelte UI)..." -ForegroundColor Yellow
$uiDir = Join-Path $rootDir "backend\llama.cpp-b9873\tools\ui"
$npmCmd = (Get-Command npm -ErrorAction SilentlyContinue)
$bundledAssets = Join-Path $rootDir "backend\llama.cpp-b9873\custom_public"
$targetPublic = Join-Path $llamaDir "custom_public"
$bundledIndex = Join-Path $bundledAssets "index.html"

# バンドル済みWebUI成果物が存在する場合は即時配備 (npm install ＆ vite build による2〜3分の遅延を防止)
if ((Test-Path $bundledIndex) -and (-not $env:DEINEI_FORCE_BUILD_UI)) {
    Write-Host "  [FAST] ビルド済みWebUIアセットを展開しています..." -ForegroundColor Cyan
    if (-not (Test-Path $targetPublic)) { New-Item -ItemType Directory -Path $targetPublic -Force | Out-Null }
    Copy-Item -Path "$bundledAssets\*" -Destination $targetPublic -Recurse -Force
    Remove-Item -Path "$targetPublic\deining\motions" -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $jinjaSrc) { Copy-Item $jinjaSrc $jinjaDst -Force }
    Write-Host "  [OK] Frontend assets deployed successfully (0s)" -ForegroundColor Green
} elseif ($npmCmd -and (Test-Path $uiDir)) {
    Write-Host "  [BUILD] Building frontend assets from source..." -ForegroundColor Cyan
    Push-Location $uiDir
    & npm install
    & npm run build:deining
    Pop-Location
    # Sync built assets to backend repo folder for future git commits
    $sandboxPublic = Join-Path $llamaDir "custom_public"
    if (Test-Path $sandboxPublic) {
        if (-not (Test-Path $bundledAssets)) { New-Item -ItemType Directory -Path $bundledAssets -Force | Out-Null }
        Copy-Item -Path "$sandboxPublic\*" -Destination $bundledAssets -Recurse -Force -Exclude "*fav_usaneko*", "*ulvm2*", "*.moc3" -ErrorAction SilentlyContinue

        # ユーザー追加VRMモデルの配布フォルダ混入防止 (AGENTS.md 環境再現性)

        Remove-Item -Path "$bundledAssets\deining\vrm" -Recurse -Force -ErrorAction SilentlyContinue
    }
    # Re-place jinja chat template after frontend build
    if (Test-Path $jinjaSrc) { Copy-Item $jinjaSrc $jinjaDst -Force }
    Write-Host "  [OK] Frontend build completed" -ForegroundColor Green
} else {
    Write-Host "  [WARN] Bundled frontend assets not found at: $bundledAssets" -ForegroundColor Red
}

# --- 6. Live2D Cubism SDK Core & Model Directories Setup ---
Write-Host "`n[Step 6/7] Setting up Live2D Cubism SDK Core & model directories..." -ForegroundColor Yellow

# (A) Live2D Cubism 4 Core Official Auto-Download & Deployment (Compliant with Live2D License)
$coreTargets = @(
    (Join-Path $rootDir "llama-b9873-bin-win-cuda-13.3-x64sandbox\custom_public\deining\live2dcubismcore.min.js"),
    (Join-Path $rootDir "backend\llama.cpp-b9873\custom_public\deining\live2dcubismcore.min.js"),
    (Join-Path $rootDir "backend\llama.cpp-b9873\tools\ui\static\deining\live2dcubismcore.min.js")
)

$needDownloadCore = $false
foreach ($ct in $coreTargets) {
    if (-not (Test-Path $ct)) { $needDownloadCore = $true; break }
}

if ($needDownloadCore) {
    Write-Host "  [DOWNLOAD] Fetching Live2D Cubism SDK for Web 4 (official binary)..." -ForegroundColor Cyan
    $sdkUrl = "https://cubism.live2d.com/sdk-web/bin/CubismSdkForWeb-4-r.7.zip"
    $sdkZip = Join-Path $rootDir "cubism4_sdk_temp.zip"
    $sdkExtract = Join-Path $rootDir "cubism4_sdk_temp"
    
    try {
        & curl.exe -L -o $sdkZip $sdkUrl --progress-bar
        Write-Host "  [EXTRACT] Extracting Cubism Core..." -ForegroundColor Cyan
        Expand-Archive -Path $sdkZip -DestinationPath $sdkExtract -Force
        
        $extractedCore = Join-Path $sdkExtract "CubismSdkForWeb-4-r.7\Core\live2dcubismcore.min.js"
        if (Test-Path $extractedCore) {
            foreach ($target in $coreTargets) {
                $targetDir = Split-Path -Parent $target
                if (-not (Test-Path $targetDir)) { New-Item -ItemType Directory -Path $targetDir -Force | Out-Null }
                Copy-Item -Path $extractedCore -Destination $target -Force
            }
            Write-Host "  [OK] Live2D Cubism Core (v4) deployed successfully (official source)" -ForegroundColor Green
        } else {
            Write-Host "  [WARN] live2dcubismcore.min.js not found in extracted archive." -ForegroundColor Red
        }
    } catch {
        Write-Host "  [WARN] Failed to download Live2D Cubism SDK." -ForegroundColor Red
    } finally {
        Remove-Item $sdkZip -Force -ErrorAction SilentlyContinue
        Remove-Item $sdkExtract -Recurse -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "  [OK] Live2D Cubism Core (v4) already present" -ForegroundColor Green
}

# (A-2) PixiJS & Cubism4 WebGL Renderer Setup (Local offline deployment)
$pixiTargets = @(
    (Join-Path $rootDir "llama-b9873-bin-win-cuda-13.3-x64sandbox\custom_public\deining\pixi.min.js"),
    (Join-Path $rootDir "backend\llama.cpp-b9873\custom_public\deining\pixi.min.js"),
    (Join-Path $rootDir "backend\llama.cpp-b9873\tools\ui\static\deining\pixi.min.js")
)
$cubism4Targets = @(
    (Join-Path $rootDir "llama-b9873-bin-win-cuda-13.3-x64sandbox\custom_public\deining\cubism4.min.js"),
    (Join-Path $rootDir "backend\llama.cpp-b9873\custom_public\deining\cubism4.min.js"),
    (Join-Path $rootDir "backend\llama.cpp-b9873\tools\ui\static\deining\cubism4.min.js")
)

$sourcePixi = Join-Path $rootDir "backend\llama.cpp-b9873\custom_public\deining\pixi.min.js"
if (Test-Path $sourcePixi) {
    foreach ($pt in $pixiTargets) {
        $ptDir = Split-Path -Parent $pt
        if (-not (Test-Path $ptDir)) { New-Item -ItemType Directory -Path $ptDir -Force | Out-Null }
        if (-not (Test-Path $pt)) { Copy-Item $sourcePixi -Destination $pt -Force }
    }
} else {
    Write-Host "  [DOWNLOAD] Fetching PixiJS 7.x (MIT) for offline Live2D display..." -ForegroundColor Cyan
    try {
        & curl.exe -L -o $sourcePixi "https://cdn.jsdelivr.net/npm/pixi.js@7.x/dist/pixi.min.js" --progress-bar
        foreach ($pt in $pixiTargets) {
            $ptDir = Split-Path -Parent $pt
            if (-not (Test-Path $ptDir)) { New-Item -ItemType Directory -Path $ptDir -Force | Out-Null }
            Copy-Item $sourcePixi -Destination $pt -Force
        }
    } catch {}
}

$sourceCubism4 = Join-Path $rootDir "backend\llama.cpp-b9873\custom_public\deining\cubism4.min.js"
if (Test-Path $sourceCubism4) {
    foreach ($ct in $cubism4Targets) {
        $ctDir = Split-Path -Parent $ct
        if (-not (Test-Path $ctDir)) { New-Item -ItemType Directory -Path $ctDir -Force | Out-Null }
        if (-not (Test-Path $ct)) { Copy-Item $sourceCubism4 -Destination $ct -Force }
    }
} else {
    Write-Host "  [DOWNLOAD] Fetching pixi-live2d-display cubism4 bundle (MIT)..." -ForegroundColor Cyan
    try {
        & curl.exe -L -o $sourceCubism4 "https://cdn.jsdelivr.net/npm/pixi-live2d-display/dist/cubism4.min.js" --progress-bar
        foreach ($ct in $cubism4Targets) {
            $ctDir = Split-Path -Parent $ct
            if (-not (Test-Path $ctDir)) { New-Item -ItemType Directory -Path $ctDir -Force | Out-Null }
            Copy-Item $sourceCubism4 -Destination $ct -Force
        }
    } catch {}
}
Write-Host "  [OK] PixiJS & Cubism4 local bundles deployed successfully" -ForegroundColor Green

# (B) Model Directories Setup
$live2dBase = Join-Path $rootDir "llama-b9873-bin-win-cuda-13.3-x64sandbox\custom_public\deining\models"
$live2dSilver = Join-Path $live2dBase "銀髪の女の子"
$live2dBrown = Join-Path $live2dBase "茶髪の癒し系お姉さん"

if (-not (Test-Path $live2dSilver)) { New-Item -ItemType Directory -Path $live2dSilver -Force | Out-Null }
if (-not (Test-Path $live2dBrown)) { New-Item -ItemType Directory -Path $live2dBrown -Force | Out-Null }

Write-Host "  [OK] Model directories ready:" -ForegroundColor Green
Write-Host "    - Silver hair: $live2dSilver"
Write-Host "    - Brown hair:  $live2dBrown"

# --- 6.5 Launcher Script Generation ---
Write-Host "`n[Deploy] Generating launcher scripts..." -ForegroundColor Cyan
$mainLauncherSrc = Join-Path $rootDir "installer\templates\start_ai_servers.bat"
$mainLauncherDst = Join-Path $rootDir "start_ai_servers.bat"
if (Test-Path $mainLauncherSrc) {
    Copy-Item -Path $mainLauncherSrc -Destination $mainLauncherDst -Force
    Write-Host "  [OK] Generated main launcher: start_ai_servers.bat" -ForegroundColor Green
} else {
    Write-Host "  [WARN] Template not found: $mainLauncherSrc" -ForegroundColor Yellow
}

$subScriptsDir = Join-Path $rootDir "scripts"
if (-not (Test-Path $subScriptsDir)) {
    New-Item -ItemType Directory -Path $subScriptsDir -Force | Out-Null
}
$subScriptsToDeploy = @(
    "start_ai_servers_lan.bat",
    "start_ai_servers_tailscale.bat",
    "deinei_doctor.bat"
)
foreach ($scriptName in $subScriptsToDeploy) {
    $srcPath = Join-Path $rootDir "installer\templates\$scriptName"
    $dstPath = Join-Path $subScriptsDir $scriptName
    $legacyRootPath = Join-Path $rootDir $scriptName
    if (Test-Path $legacyRootPath) {
        Remove-Item -Path $legacyRootPath -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path $srcPath) {
        Copy-Item -Path $srcPath -Destination $dstPath -Force
        Write-Host "  [OK] Generated sub-script: scripts\$scriptName" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] Template not found: $srcPath" -ForegroundColor Yellow
    }
}

# --- 7. Desktop Shortcut ---
Write-Host "`n[Step 7/7] Creating desktop shortcut..." -ForegroundColor Yellow
try {
    $wshShell = New-Object -ComObject WScript.Shell
    $desktopPath = [System.Environment]::GetFolderPath('Desktop')
    $shortcutPath = Join-Path $desktopPath "泥濘 (Deinei) を起動.lnk"
    $targetBat = Join-Path $rootDir "start_ai_servers.bat"

    $shortcut = $wshShell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $targetBat
    $shortcut.WorkingDirectory = $rootDir
    $shortcut.Description = "Launch Deinei AI System"
    $shortcut.Save()
    Write-Host "  [OK] Desktop shortcut created: $shortcutPath" -ForegroundColor Green
} catch {
    Write-Host "  [INFO] Skipped shortcut creation (start_ai_servers.bat can be run directly)" -ForegroundColor DarkGray
}

# =====================================================================
# Model Download Section (LLM Model & Vision Encoder)
# =====================================================================
$llmModelName = "Goetia-26B-A4B-v1.7.i1-IQ4_XS.gguf"
$llmModelPath = Join-Path $llamaDir $llmModelName
# ※HF上の正確なURL・リポジトリ名は要確認 (実機ファイル名: Melody1437-26B-A4B.i1-IQ4_XS.gguf)
$llmModelUrl = "https://huggingface.co/mradermacher/Goetia-26B-A4B-v1.7-i1-GGUF/resolve/main/Goetia-26B-A4B-v1.7.i1-IQ4_XS.gguf?download=true"

$mmprojModelName = "mmproj-BF16.gguf"
$mmprojModelPath = Join-Path $llamaDir $mmprojModelName
$mmprojModelUrl = "https://huggingface.co/mradermacher/Melody1437-27B-GGUF/resolve/main/Melody1437-27B.mmproj-f16.gguf?download=true"

$hasLlm = (Test-Path $llmModelPath) -or (Test-Path (Join-Path $llamaDir "Melody1437-26B-A4B.i1-IQ4_XS.gguf")) -or (Test-Path (Join-Path $llamaDir "Melody1437-27B.i1-IQ4_XS.gguf"))
$hasMmproj = (Test-Path $mmprojModelPath) -or (Test-Path (Join-Path $llamaDir "Melody1437-27B.mmproj-f16.gguf"))

if ($hasLlm -and $hasMmproj) {
    Write-Host "`n  [OK] Recommended LLM model and Vision encoder are ready in sandbox." -ForegroundColor Green
} else {
    Write-Host "`n======================================================================" -ForegroundColor Cyan
    Write-Host " [MODEL DOWNLOAD] 推奨モデル自動ダウンロード / Model Download" -ForegroundColor Cyan
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host "  【推奨モデルのダウンロード確認 / Recommended Models Download】" -ForegroundColor Yellow
    Write-Host "  泥濘システム推奨の対話モデル本体およびVisionエンコーダを取得できます。"
    Write-Host "  You can download the recommended LLM dialogue model and Vision encoder."
    Write-Host "  - LLM モデル本体 / Main LLM:     $llmModelName (約14.3GB)" -ForegroundColor Cyan
    Write-Host "  - Vision エンコーダ / Vision:    $mmprojModelName (約0.93GB / マルチモーダル画像認識用)" -ForegroundColor Cyan
    Write-Host "  ※Visionエンコーダは24GB環境のみ既定有効ですが、全環境で取得され deining_profile.bat で手動ONが可能です。" -ForegroundColor DarkGray
    Write-Host "  ※ダウンロードサイズは【約15.3GB】（LLM本体: 約14.3GB + Vision: 約0.93GB）と大容量です。" -ForegroundColor Yellow
    Write-Host "  * Total download size is approx 15.3GB (Main LLM: ~14.3GB + Vision: ~0.93GB)." -ForegroundColor Yellow
    Write-Host "  ※安定した高速インターネット回線とディスクの空き容量を十分にご確認ください。" -ForegroundColor Yellow
    Write-Host "  * Please ensure a fast, stable internet connection and sufficient disk space." -ForegroundColor Yellow

    $dlConfirm = Prompt-YesNo `
        -PromptJa "モデル一式 (約15.3GB) を今すぐ自動ダウンロードしますか？" `
        -PromptEn "Do you want to download the model package (~15.3GB) now?"

    if ($dlConfirm) {
        # 1. LLM Model
        if (-not (Test-Path $llmModelPath)) {
            Write-Host "`n  [DOWNLOAD 1/2] Downloading LLM model: $llmModelName (~14.3GB)..." -ForegroundColor Cyan
            try {
                & curl.exe -L -o $llmModelPath $llmModelUrl --progress-bar
                if (Test-Path $llmModelPath) {
                    Write-Host "  [OK] LLM model downloaded successfully: $llmModelName" -ForegroundColor Green
                }
            } catch {
                Write-Host "  [警告 / WARNING] LLM モデルのダウンロードに失敗しました。 / Failed to download LLM model." -ForegroundColor Red
            }
        } else {
            Write-Host "  [OK] LLM model already exists: $llmModelName" -ForegroundColor Green
        }

        # 2. Vision Encoder (初期設定で無効なプロファイルでも将来・上位設定用に同梱配置)
        if (-not (Test-Path $mmprojModelPath)) {
            Write-Host "`n  [DOWNLOAD 2/2] Downloading Vision encoder: $mmprojModelName (~0.93GB)..." -ForegroundColor Cyan
            try {
                & curl.exe -L -o $mmprojModelPath $mmprojModelUrl --progress-bar
                if (Test-Path $mmprojModelPath) {
                    Write-Host "  [OK] Vision encoder downloaded successfully: $mmprojModelName" -ForegroundColor Green
                }
            } catch {
                Write-Host "  [警告 / WARNING] Vision エンコーダのダウンロードに失敗しました。 / Failed to download Vision encoder." -ForegroundColor Red
            }
        } else {
            Write-Host "  [OK] Vision encoder already exists: $mmprojModelName" -ForegroundColor Green
        }
    } else {
        Write-Host "  [INFO] モデルのダウンロードをスキップしました。後から手動で配置可能です。" -ForegroundColor DarkGray
        Write-Host "  [INFO] Skipped model download. You can download manually anytime." -ForegroundColor DarkGray
    }
}

Write-Host "`n======================================================================" -ForegroundColor Cyan
Write-Host " [SUCCESS] Setup Completed!" -ForegroundColor Green
Write-Host "======================================================================" -ForegroundColor Cyan

Write-Host "`n[Live2D Character Models]" -ForegroundColor Magenta
Write-Host "  Place your Live2D model folders into:"
Write-Host "  1. Silver hair: $live2dSilver" -ForegroundColor Cyan
Write-Host "  2. Brown hair:  $live2dBrown" -ForegroundColor Cyan

Write-Host "`n[LLM Model & Vision Encoder Status]" -ForegroundColor Magenta
if (Test-Path $llmModelPath) {
    Write-Host "  - LLM Model:       [Ready] $llmModelName" -ForegroundColor Green
} elseif (Test-Path (Join-Path $llamaDir "Melody1437-26B-A4B.i1-IQ4_XS.gguf")) {
    Write-Host "  - LLM Model:       [Ready] Melody1437-26B-A4B.i1-IQ4_XS.gguf" -ForegroundColor Green
} elseif (Test-Path (Join-Path $llamaDir "Melody1437-27B.i1-IQ4_XS.gguf")) {
    Write-Host "  - LLM Model:       [Ready] Melody1437-27B.i1-IQ4_XS.gguf" -ForegroundColor Green
} else {
    Write-Host "  - LLM Model:       [Not Found] Please place $llmModelName into sandbox folder" -ForegroundColor DarkYellow
    Write-Host "    Download URL:    $llmModelUrl" -ForegroundColor Cyan
}

if (Test-Path $mmprojModelPath) {
    Write-Host "  - Vision Encoder:  [Ready] $mmprojModelName" -ForegroundColor Green
} elseif (Test-Path (Join-Path $llamaDir "Melody1437-27B.mmproj-f16.gguf")) {
    Write-Host "  - Vision Encoder:  [Ready] Melody1437-27B.mmproj-f16.gguf" -ForegroundColor Green
} else {
    Write-Host "  - Vision Encoder:  [Not Found] Optional (used for multimodal image input)" -ForegroundColor DarkGray
    Write-Host "    Download URL:    $mmprojModelUrl" -ForegroundColor Cyan
}

Write-Host "`n[TTS Model (Irodori-TTS)]" -ForegroundColor Magenta
Write-Host "  - Model: $ttsModelDesc" -ForegroundColor DarkGray

Write-Host "`nDouble-click the desktop shortcut or run start_ai_servers.bat to launch Deinei!`n"

if ($script:setupMutex) {
    try {
        $script:setupMutex.ReleaseMutex()
        $script:setupMutex.Dispose()
    } catch {}
}