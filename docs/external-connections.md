# 外部通信一覧仕様書 (External Connections)

本書は、泥濘 (Deinei) システムにおける外部ネットワーク通信の送信先、プロトコル、発火条件、および制御方法を漏れなく定義した仕様書です。

---

## 1. 概要
泥濘は原則としてローカル完結型AIアシスタントであり、ローカルLLM (llama-server: 8080) およびローカルTTS (VOICEVOX等: 5001) を中核として動作します。
外部インターネットへの接続が発生するのは、**「ユーザーが意図してWeb検索機能をONにし、明示的に検索・天気を指示したとき」** または **「初回インストール・モデルダウンロード時」** に限定されます。

---

## 2. 外部通信先一覧

### 2.1 Web検索・気象データ (実行時 / 検索トグルON時のみ)

| ドメイン | 用途 | 送信元 | トリガー条件 | 遮断・制御方法 |
| :--- | :--- | :--- | :--- | :--- |
| `duckduckgo.com`<br>`api.duckduckgo.com`<br>`html.duckduckgo.com`<br>`lite.duckduckgo.com` | Web検索・ニュース検索 (DuckDuckGo DDGS) | `server.py` (`_do_web_search`) | Web検索トグルON かつ 明示的な検索指示文（「〜を検索して」「〜について調べて」「ググって」等） | UIの検索トグルをOFFにするか、環境変数 `DEINEI_SEARCH_ENABLED=0` |
| `nominatim.openstreetmap.org` | 天気予報用の地名緯度経度変換 (Geocoding) | `server.py` (`get_weather_forecast`) | Web検索トグルON かつ 明示的な天気指示文（「東京の天気を教えて」「明日の天気予報」等） | UIの検索トグルをOFFにする |
| `api.open-meteo.com` | リアルタイム気象データ取得 (Open-Meteo) | `server.py` (`get_weather_forecast`) | 上記の地名解決後、気象予報取得時 | UIの検索トグルをOFFにする |
| `ja.wikipedia.org` | 百科事典・概要検索フォールバック | `server.py` (`_do_web_search`) | Web検索トグルON かつ DDGS検索が0件 かつ **環境変数 `DEINEI_WIKI_FALLBACK=1` が設定されている場合のみ** (既定無効) | 既定で無効化。有効化フラグを未設定または `0` にする |

> [!NOTE]
> - **検索トグルOFF時の動作**: UI上で検索トグルがOFFになっている場合、`should_check_search` が `False` となり、上記すべての外部通信（DDGS, Open-Meteo, Nominatim, Wikipedia）に一切到達しません。
> - **曖昧な入力の除外**: 「教えて」「いい天気だね」「今何時」「今日は何日」等の日常会話では外部検索は発火しません。

---

### 2.2 セットアップ・初回ダウンロード時 (開発・導入時のみ)

| ドメイン | 用途 | 実行契機 | 制御方法 |
| :--- | :--- | :--- | :--- |
| `huggingface.co` | GGUFモデルファイル・マルチモーダルプロジェクタのダウンロード | 初回起動時、ローカルにモデルファイルが存在しない場合 (`install.bat`, 手動DL) | モデルを手動で事前配置することで通信不要 |
| `pypi.org`<br>`files.pythonhosted.org` | Pythonパッケージ依存関係のインストール | `install.bat` 実行時 (`pip install -r requirements.txt`) | オフラインホイールまたは事前環境構築で回避可能 |
| `registry.npmjs.org` | フロントエンドビルド用npmパッケージの取得 | 初回UIビルド時 (`npm install`) | 事前ビルド済み静的アセット利用時は不要 |

---

### 2.3 遮断・無効化された通信 (セキュリティ強化)

| ドメイン | 過去の状態 | 現在の状態 |
| :--- | :--- | :--- |
| `mcp.exa.ai` | UIの既定MCPプリセットとして登録されていた | **完全削除 (空配列化)**。ユーザーが明示的に追加しない限り接続しません。 |
| `huggingface.co/mcp` | UIの既定MCPプリセットとして登録されていた | **完全削除 (空配列化)**。ユーザーが明示的に追加しない限り接続しません。 |

---

### 2.4 ローカル内通信 (外部インターネット非経由)

すべて `127.0.0.1` または `localhost` (同一ホスト内部ループバック) に限定されます。

- `http://127.0.0.1:8080`: llama-server (LLM推論API・WebUI配信)
- `http://127.0.0.1:5000`: Deinei Backend (RAG・記憶・セッション・プロキシAPI)
- `http://127.0.0.1:5001`: VOICEVOX / ローカルTTSサーバー
- UIのCSP (`Content-Security-Policy`): `connect-src 'self' http://127.0.0.1:5000 http://localhost:5000 http://127.0.0.1:5001 http://localhost:5001 blob: data:;` により、ブラウザからの意図しない外部オリジンへの通信を多重防御しています。
