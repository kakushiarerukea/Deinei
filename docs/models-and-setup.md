# モデル準備・詳細設定・アンインストール仕様書 (Models & Setup)

本書は、泥濘 (Deinei) システムで利用する各モデル（LLM、Vision、Live2D、TTS）の仕様および手動配置方法、ハードウェアプロファイル設定、ならびにアンインストール・クリーンアップ手順を定義したドキュメントです。

---

## 1. 必要なモデル一覧と手動配置先

インストーラー実行時にモデルの自動ダウンロードをスキップした場合や、手動でファイルを配置・差し替えしたい場合は、下記を参照してください。

### 1.1 LLM メインモデル (.gguf)
- **推奨モデル**: `Goetia-26B-A4B-v1.7.i1-IQ4_XS.gguf`（約14.3GB / 26Bパラメータ / Naphula氏によるRP・論理推論特化マージ / 実測平均約65 TPS）
- **配置先**: `llama-b9873-bin-win-cuda-13.3-x64sandbox/` 直下
- **ダウンロード元**: [Hugging Face: Goetia-26B-A4B-v1.7-i1-GGUF](https://huggingface.co/mradermacher/Goetia-26B-A4B-v1.7-i1-GGUF)

### 1.2 Vision エンコーダ (画像認識用)
- **推奨モデル**: `mmproj-BF16.gguf`（約1.19GB。Gemma 4 / Goetia 26B 適合マルチモーダルプロジェクタ）
- **配置先**: `llama-b9873-bin-win-cuda-13.3-x64sandbox/` 直下
- **ダウンロード元**: [Hugging Face: unsloth/gemma-4-26B-A4B-it-GGUF (mmproj-BF16.gguf)](https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF)
- **有効化設定**:
  - Visionエンコーダは環境構築時に一括取得されますが、VRAM消費を抑え最速の推論速度を維持するため、既定では24GB（Ultra）プロファイルでのみ有効化されます。
  - 22GB未満環境（8GB〜16GB）でも、`deining_profile.bat` の `MMPROJ_ARGS` を手動編集（`set MMPROJ_ARGS=--mmproj mmproj-BF16.gguf`）することで手動有効化が可能です。

### 1.3 Live2D キャラクターモデル
- **対応モデル**: Usaneko式 無料Live2Dモデル
  - 茶髪モデル: [Usaneko式「茶髪の癒し系お姉さん」 (vtem.jp)](https://vtem.jp/listing/model_0005_favorite/)
  - 銀髪モデル: [Usaneko式「銀髪の女の子」 (vtem.jp)](https://vtem.jp/listing/ulvm2_0001/)
  - ※モデルの利用・配信にあたっては、上記配布元の利用規約（クレジット表記、非商用範囲等）を遵守してください。
- **ドラッグ＆ドロップ配置 (推奨)**:
  - WebUI左ペインの「Live2Dモデルが未配置です」画面に、ダウンロードしたZIPファイルをそのままドラッグ＆ドロップ（または枠をクリックしてファイル選択）することで自動展開・配置されます。
- **手動配置先**:
  - 銀髪モデル: `llama-b9873-bin-win-cuda-13.3-x64sandbox/custom_public/deining/ulvm2_0001/`
  - 茶髪モデル: `llama-b9873-bin-win-cuda-13.3-x64sandbox/custom_public/deining/fav_usaneko_alwb/`

### 1.4 TTS 音声合成モデル (Irodori-TTS)
- **推奨モデル**:
  - **24GB (Ultra) 環境**: `Aratako/Irodori-TTS-v4-Small` (BF16, 約3.0GB)
  - **16GB以下 (8GB〜16GB) 環境**: `Aratako/Irodori-TTS-v4-Small-Quantized` (float8 weight-only, 約873MB)
- **配置先**: `backend/Irodori-TTS/models/model.safetensors`
- **ダウンロード元**:
  - 24GB: [Hugging Face: Aratako/Irodori-TTS-v4-Small](https://huggingface.co/Aratako/Irodori-TTS-v4-Small)
  - 16GB以下: [Hugging Face: Aratako/Irodori-TTS-v4-Small-Quantized](https://huggingface.co/Aratako/Irodori-TTS-v4-Small-Quantized)

---

## 2. ハードウェア別プロファイル (`deining_profile.bat`)

インストーラー（`setup.ps1`）は、検出されたGPUのVRAM容量に応じて `deining_profile.bat` を自動生成します。

| VRAMクラス | 判定基準 | 想定ハードウェア | MoE CPU退避数 (-ncmoe) | TTSモデル形式 | Vision エンコーダ |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **8GB〜10GB (Low/Medium)** | `< 11.0GB` | RTX 3060(8G) / 4060 / 5060(8G) / 5060Ti(8G) | 20層オフロード | float8 量子化 (~873MB) | 無効（手動有効化可能） |
| **12GB (Medium)** | `>= 11.0GB` | RTX 2060 12GB / 3060(12G) / 4070 / 5070 | 16層オフロード | float8 量子化 (~873MB) | 無効（手動有効化可能） |
| **16GB (High)** | `>= 13.0GB` | RTX 4060Ti(16G) / 4080(16G) / 5060Ti(16G) / 5070Ti(16G) / 5080(16G) | 9層オフロード | float8 量子化 (~873MB) | 無効（手動有効化可能） |
| **24GB (Ultra)** | `>= 22.0GB` | RTX 3090 / 4090 / 5090(32G) | 0層（全層GPU） | BF16 非量子化 (~3.0GB) | 有効 (`--mmproj mmproj-BF16.gguf`) |

### 2.1 起動要件の実測値（2026-10-20）

RTX 5070 Ti (16GB VRAM) / 32GB RAM / NVIDIA Driver 616.92 / 16GBプロファイルを手動調整（`-ncmoe 12`、Vision有効）/ TTS float8 の環境で、起動直後のプロセスを実測した値です。
- 推論速度（TPS）は前モデル（Melody1437: 約70 TPS）に対し、新モデル（Goetia-26B-v1.7）では平均約65 TPS（約5 TPS低下）となりますが、マージされたGryphe/Pantheon-Reasoningにより事実抽出（Fact Extraction）および指示追従の精度が大幅に向上しています。

| 項目 | 実測値 | 補足 |
| :--- | :--- | :--- |
| LLM (`llama-server.exe`) | コミット約18.4GB | 24GB以上RAM環境: `--no-mmap` でモデルを物理RAM常駐。24GB未満RAM環境: 自動で `mmap` モードへ切り替わりコミットメモリを削減 |
| TTS (`tts_server.py`) | コミット約11.8GB | float8モデル + CUDAコンテキスト |
| RAG (`server.py`) | コミット約3.4GB | 埋め込みモデル + ChromaDB |
| 合計 | コミット約33.6GB | 32GB RAM + ページファイルで稼働 |

- コミットは `Get-Process` の Private Memory の実測値です。ワーキングセットはOSのトリミングで変動するため、要件の判定には使いません。
- 物理RAMは32GB以上を推奨します（全プロセスを物理RAM内に余裕で常駐可能）。16GB環境でも、十分なページファイルとSSD空き容量を確保することで、VRAM 8GB + RAM 16GB環境にて実測約30 TPSの動作を確認しています（SSDの空き容量・ページファイルの事前確保を推奨）。
- **動的mmapハイブリッド制御**: ランチャー（GUI / CLI / API Bridge）は起動時にシステムの総物理RAM容量を自動判定します。RAMが24.0GB未満の環境では、LLMサーバーの起動引数から `--no-mmap` を自動除外して `mmap` モード（OSページキャッシュ共有）で起動し、約14GBのコミットメモリ膨張による仮想メモリ枯渇（`os error 1455`）を防ぎます。24.0GB以上の環境では `--no-mmap` を維持して物理メモリ常駐を行い、ディスク読み出しによる推論遅延を防止します。ランチャー起動ログには `RAM XX.XGB (mmap節約モード)` または `RAM XX.XGB (--no-mmap常駐)` が明記されます。
- 数値はプロファイルで変動します。24GBプロファイルはTTSがBF16（モデル約3.1GB）になり、Visionが有効になります。8GBプロファイルは `-ncmoe 20` でCPU側のRAM使用量が増えます。
- ストレージは、モデル（LLM 14.3GB + mmproj 1.19GB + TTS 0.9GB/3.1GB）とPython環境（PyTorch CUDA含む）、キャッシュを含めて30GB以上（推奨40GB）を確保してください。実測したリポジトリ全体は約24.0GB（TTS用venv 5.0GB、HFキャッシュ1.7GBを含む）です。統合venv構成ではこれより増えます。
- CUDA Toolkitのインストールは不要です（`cudart-llama-bin-win-cuda-13.3-x64.zip` のDLLを同梱）。Python 3.10は `uv` が自動取得し、Node.jsも不要です（WebUIはビルド済み）。
- インストーラーが自動判定するのはVRAMのみです。RAM・空き容量・ドライバ・AVX2・WebView2・VC++ランタイムは自動検証しません。READMEの動作要件表とあわせて事前に確認してください。

---

## 3. 初回起動時の初期設定ウィザード (Initial Setup Wizard)

WebUI（ブラウザ）へ初回アクセスした際、初期設定ダイアログ（`DialogInitialSetup`）が自動的にポップアップ表示されます。環境に合わせて直感的に初期構成を完了できます。

- **表示言語**: 日本語（ja）または 英語（en）を選択可能です。UI全体のテキストおよびシステムプロンプトの既定値が切り替わります。
- **音声読み上げ (TTS)**: 音声合成の有効/無効、およびアバターリップシンク字幕との同期（TTS Sync）の有効/無効を設定できます。
- **Web検索機能**: DuckDuckGo / Open-Meteo 連携の初期状態を選択できます（既定はOFF）。有効化時の検索トリガーキーワード（「検索して」「調べて」「天気予報」など）の解説が表示されます。
- **システムプロンプト設定**:
  - サンプルプロンプトの利用（泥濘標準の照れ隠し・内向的なキャラクター設定）またはカスタムプロンプトの直接入力・新規作成を選択できます。
- **設定後の変更**: 初回完了後も、WebUI左ペインの歯車アイコン（設定ダイアログ）からいつでもすべてのパラメータを再編集できます。

---

## 4. システムのアップデート手順 (Updating)

泥濘は、Gitリポジトリの最新ソースコード、Python仮想環境、およびUI成果物を安全に一括更新するワンクリックアップデーターを同梱しています。

### 4.1 アップデートの実行
1. 泥濘の各サーバープロセスが稼働中の場合は終了します（多重起動防止ミューテックスにより、起動中の場合は警告が表示されます）。
2. プロジェクト直下の **`update.bat`** をダブルクリックして実行します（内部で `installer/update.ps1` が呼び出されます）。
3. 最新コミットの取得（`git pull`）、`uv` による Python 依存関係の同期、WebUI配布用静的アセットの整合チェックが自動進行します。
4. アップデート完了後、通常通り `deinei_launcher.py` または `start_ai_servers.bat` から起動できます。

### 4.2 ユーザーデータの保護
アップデーターはシステム基幹コードと依存物のみを更新します。以下のユーザー個別データは保持され、上書き・削除されることはありません：
- **対話ログ・会話ツリー**: `backend/data/chats/` 内の JSONL ファイル群およびブラウザ IndexedDB
- **長期記憶ベクトルDB**: `backend/data/chroma_db/`
- **ダウンロード済みモデル**: `llama-b9873-bin-.../` 直下の LLM（`.gguf`）および `backend/Irodori-TTS/models/` 内の音声重み
- **配置済みアバターモデル**: `custom_public/deining/` 内の Live2D / VRM ファイル
- **ユーザー個別設定**: `deining_profile.bat`、ブラウザ内設定（`localStorage`）

---

## 5. アンインストール・クリーンアップ手順 (Uninstallation)

泥濘がインストール・稼働時に生成する各種キャッシュ（`uv` パッケージキャッシュ、スタンドアロン Python、`pip` キャッシュ、Hugging Face モデルキャッシュ、PyTorch キャッシュ、ランチャー WebView2 キャッシュ）はすべてプロジェクト直下の **`.cache/`** フォルダに集約されています。

### 5.1 実行手順
1. フォルダ直下の **`uninstall.bat`** をダブルクリックして実行します。
2. 稼働中の泥濘サーバープロセスがある場合は自動停止し、ファイルロックを解除します。
3. 以下の項目について、**削除するか残す（保持する）かを個別に選択（`y` / `n`）** できます（誤削除防止のため既定はすべて `n`: 保持）：
   - **会話履歴・長期記憶データ**: ブラウザ内の会話ツリー（IndexedDB）および PC 内の `backend/data/chats/`、`backend/data/chroma_db/`
   - **WebUI上で変更したパラメータ・設定・Cookie**: ブラウザ内の `localStorage`（システムプロンプト、生成パラメータ、Live2D 設定等）・`Cookie`・`ServiceWorker`、および `deining_profile.bat`
   - **導入済みアバターモデルデータ (Live2D / VRM)**: `custom_public/deining/` 内のモデルファイル（Live2D モデルおよび VRM モデル）
4. 確認後に処理を実行すると、`.cache/`、`backend/.venv/`、LLM・TTS の大容量モデル重み、実行バイナリ、デスクトップショートカットが一括削除されます。

### 5.2 外部環境への配慮
アンインストーラーは泥濘フォルダ外の PC 共通領域（システム Python、ユーザーホーム直下のキャッシュディレクトリ、Tailscale 本体、他サイトのブラウザ Cookie 等）には変更を加えません。
