# 外部通信一覧仕様書 (External Connections)

本書は、泥濘 (Deinei) システムにおける外部ネットワーク通信の送信先、プロトコル、発火条件、および制御方法を漏れなく定義した仕様書です。

---

## 1. 概要
泥濘は原則としてローカル完結型AIアシスタントであり、ローカルLLM (llama-server: 8080) およびローカルTTS (Irodori-TTS: 5001) を中核として動作します。
外部インターネットへの接続が発生するのは、**「ユーザーが意図してWeb検索機能をONにし、明示的に検索・天気を指示したとき」**、**「初回インストール・モデルダウンロード時」**、または **「音声認識 (STT) 初回利用時のモデル展開」** に限定されます。

---

## 2. 外部通信先一覧

### 2.1 Web検索・気象データ (実行時 / 検索トグルON時のみ)

| ドメイン | 用途 | 送信元 | トリガー条件 | 遮断・制御方法 |
| :--- | :--- | :--- | :--- | :--- |
| `duckduckgo.com`<br>`api.duckduckgo.com`<br>`html.duckduckgo.com`<br>`lite.duckduckgo.com` | Web検索・ニュース検索 (DuckDuckGo DDGS) | `server.py` (`_do_web_search`) | Web検索トグルON かつ 正規表現ゲート通過 かつ 明示的な検索指示文（「〜を検索して」「〜について調べて」「ググって」等） | UIの検索トグルをOFFにする（またはAPIリクエストの `search_enabled` を `false` にする） |
| `nominatim.openstreetmap.org` | 天気予報用の地名緯度経度変換 (Geocoding) | `server.py` (`get_weather_forecast`) | Web検索トグルON かつ 明示的な天気指示文（「東京の天気を教えて」「明日の天気予報」等） | UIの検索トグルをOFFにする |
| `api.open-meteo.com` | リアルタイム気象データ取得 (Open-Meteo) | `server.py` (`get_weather_forecast`) | 上記の地名解決後、気象予報取得時 | UIの検索トグルをOFFにする |
| `ja.wikipedia.org` | 百科事典・概要検索フォールバック | `server.py` (`_do_web_search`) | Web検索トグルON かつ DDGS検索が0件（既定で有効、DDGS結果が得られない場合のフォールバックとして動作） | 無効化する場合は環境変数 `DEINEI_WIKI_FALLBACK=0` を設定する |

> [!NOTE]
> - **検索トグルOFF時の動作**: UI上で検索トグルがOFFになっている場合、`should_check_search` が `False` となり、上記すべての外部通信（DDGS, Open-Meteo, Nominatim, Wikipedia）に一切到達しません。
> - **曖昧な入力の除外**: 「教えて」「いい天気だね」「今何時」「今日は何日」等の日常会話では外部検索は発火しません。

---

### 2.2 セットアップ・初回ダウンロード時 (開発・導入時のみ)

| ドメイン | 用途 | 実行契機 | 制御方法 |
| :--- | :--- | :--- | :--- |
| `huggingface.co` | GGUFモデル、埋め込みモデル (`ruri-v3-310m`)、TTSモデル、および音声認識モデル (`ReazonSpeech-K2`) のダウンロード | 初回セットアップ時 (`setup.ps1`)、または初回STT音声入力時 | 各モデルを手動で事前配置することで通信不要 |
| `pypi.org`<br>`files.pythonhosted.org`<br>`download.pytorch.org` | PythonパッケージおよびPyTorch CUDAバイナリ依存関係のインストール | `setup.ps1` 実行時 (`uv pip install`) | オフラインホイールまたは事前環境構築で回避可能 |
| `github.com`<br>`astral.sh` | 高速Python環境管理ツール `uv` および `llama-server` バイナリの自動取得 | `setup.ps1` 実行時 (ローカルに未存在の場合) | 手動でバイナリを事前配置することで通信不要 |
| `cubism.live2d.com` | Live2D Cubism SDK for Web 4 Core (`live2dcubismcore.min.js`) の公式ダウンロード | `setup.ps1` 実行時 (再配布不可規約に準拠) | 公式よりダウンロードして `custom_public/deining/` に手動配置可能 |
| `cdn.jsdelivr.net` | Live2D 描画用 PixiJS 7.x および pixi-live2d-display のスクリプト | 原則ローカル配置済み (`custom_public/deining/`)。ファイル欠落時のフォールバックとしてのみ使用 | ローカルファイルが存在する場合は一切通信しません |
| `fonts.googleapis.com`<br>`fonts.gstatic.com` | 統合ランチャー GUI 表示用 Web フォント取得 | ランチャー GUI 起動時 (pywebview) | システムフォールバックフォントで表示可能 |
| `registry.npmjs.org` | フロントエンドビルド用npmパッケージの取得 | 初回UIビルド時 (`npm install`) | 事前ビルド済み静的アセット利用時は不要 |

---

### 2.3 遮断・無効化された通信 (セキュリティ強化)

| ドメイン | 過去の状態 | 現在の状態 |
| :--- | :--- | :--- |
| `mcp.exa.ai` | UIの既定MCPプリセットとして登録されていた | 削除 (空配列化)。ユーザーが明示的に追加しない限り接続しません。 |
| `huggingface.co/mcp` | UIの既定MCPプリセットとして登録されていた | 削除 (空配列化)。ユーザーが明示的に追加しない限り接続しません。 |

---

### 2.4 ローカル内および端末間通信 (外部中継サーバー非経由)

本ソフトウェアは「通信がローカル（自己の管理下）で完結する」原則に従い、第三者の中継サーバー（Cloudflare Tunnel, ngrok 等）は一切使用しません。

- **ローカル内部ループバック (127.0.0.1)**:
  - `http://127.0.0.1:8080`: llama-server (LLM推論API・WebUI配信)
  - `http://127.0.0.1:5000`: Deinei Backend (RAG・記憶・セッション・プロキシAPI)
  - `http://127.0.0.1:5001`: Irodori-TTS 音声合成サーバー
  - ※上記3サーバーは常に `127.0.0.1` にのみバインドされ、外部ネットワークには直接露出しません。

- **スマートフォン・他端末連携 (認証ゲートウェイ: 8090)**:
  - `start_ai_servers_lan.bat` または `start_ai_servers_tailscale.bat` 起動時のみ、認証リバースプロキシ（`gateway.py`）が単一ポート（8090）で待受を行います。
  - **LAN モード (`--lan`)**: 同一Wi-Fiネットワーク上の端末と直接通信（`0.0.0.0:8090`）。
  - **Tailscale モード (`--tailscale`)**: Tailscale 仮想インターフェースのプライベートIP（`100.x.x.x:8090`）でのみ待受（`0.0.0.0` にはバインドしません）。端末間暗号化メッシュ（WireGuard ベース）による直接P2P通信。
  - **トークン認証**: 起動毎に暗号学的乱数で生成されたトークンによるURLおよびCookie認証を必須とし、未認証アクセスは401で即時遮断します。

- **UIのCSP (`Content-Security-Policy`)**:
  - `connect-src 'self' http://127.0.0.1:5000 http://localhost:5000 http://127.0.0.1:5001 http://localhost:5001 blob: data:;` により、ブラウザからの意図しない外部オリジンへの通信を防御しています（ゲートウェイ経由時は同一オリジン相対パス `/__deinei/api`, `/__deinei/tts` として中継）。
