# AGENTS.md — 泥濘（Deinei）

このファイルは、AIエージェント（Antigravity / Cursor / Claude Code など）が本リポジトリで作業するときの共通ルールです。人間の貢献者もこの内容に従ってください。

プロジェクト名は Deinei。`build:deining`、`deining_profile.bat`、`custom_public/deining/` など、識別子の一部は deining 表記のまま使われている。名前を統一する目的で書き換えない。

## 構成

アーキテクチャや仕様の原則は本ファイルおよび README.md に集約されている。各コンポーネントの仕様やインバリアント（不変条件）は本ドキュメントの各節およびソースコードの docstring を参照すること。（※メンテナー環境に assetsForAntigravity/ がローカル配置されている場合のみ、内部リファレンスとして参照・更新する）

- WebUIのソース: `backend/llama.cpp-b9873/tools/ui/src/`（Svelte 5 / Vite / Tailwind）
- `backend/data/server.py`（Flask, 5000番）: 記憶（ChromaDB）、要約、Web検索、会話データマスター同期（`chats/`）
- `backend/data/gateway.py`（8090番）: 独立認証ゲートウェイ（LAN / Tailscale リモートアクセス用のリバースプロキシ）
- `backend/data/` の `session_validator.py` と `search_trigger.py`: `server.py` に依存しない純粋関数
- `backend/Irodori-TTS/tts_server.py`（5001番）: TTS
- `llama-server.exe`（8080番）: LLM推論と、`custom_public` の静的ファイル配信
- `installer/`: `setup.ps1`（ハードウェア判定と `deining_profile.bat` の生成）と、`installer/templates/` の起動バッチのテンプレート
- `tests/`: 単体テストと回帰テスト
- 旧コンポーネント（`sillytavern_proxy.py`、`data/discord_bot.py`）は、現在のWebUI構成と接続していない。変更しない

## 書き方のルール（変更履歴・README・コミットメッセージ）

- 絵文字を使わない。コミットメッセージ、変更履歴、コードコメント、ドキュメント、CLI出力ログ（バッチ・PowerShell・Python）のすべてで絵文字を禁止し、`[OK]`、`[WARN]`、`[ERROR]`、`[INFO]` などの角括弧プレフィックスを使う（Windowsの cmd.exe や PowerShell 5.1 コンソールでの文字化け・列幅ズレ・パース事故を防止するため）
- 次の断言語は使わない: 「100%」「完全」「根絶」「保証」「恒久」「完璧」「絶対」。代わりに測定値や条件を書く
- 効果は数値で書く。実機ログの値（`cache_n`、`n_tokens`、TTFT、TPS）を使い、測っていない数値は書かない
- 検証は「実行コマンド → 結果」の形で書く（例: `python tests/xxx.py → 12 passed`）
- 「ビルドが通った」は動作の検証ではない。ビルドだけの場合は「動作は未検証」と明記する
- 実行していない検証は書かない。未検証の点は「未検証」として残す
- 変更履歴のエントリは次の項目で書く:
  1. 概要
  2. 原因（ログや数値の根拠付き）
  3. 変更内容（ファイル名と関数名）
  4. 影響範囲（この変更で挙動が変わりうる箇所）
  5. 検証（コマンドと結果、または手動手順）
  6. 未検証の点・既知の制限

## 変更の進め方

- 変更の前に、触るファイル・関数の呼び出し元と依存先を列挙して報告する
- 1回の変更は1つの目的に絞る。無関係なリファクタを混ぜない
- 原因が確定していない段階で修正を入れない。仮説と確認方法を先に書き、ログで確かめてから直す
- 試した結果、効果が小さい・副作用が大きいと分かった変更は、ロールバックして理由を変更履歴に残す（例: 変更履歴 40）
- バグ修正では、再現するテストを先に書く

## 壊れやすい領域

### 要約・スナップショット

`lastSummarizedIndex`、`aiHistoryTextList`、メッセージごとのスナップショットは、複数のファイル（`chat.svelte.ts` / `conversations.svelte.ts` / `database.service.ts`）で読み書きされる。次の条件を常に満たすこと。

- `0 <= lastSummarizedIndex <= 対話メッセージ件数`
- 要約ブロックが0件のとき、`lastSummarizedIndex` は 0
- 削除・ブランチ作成・Rebuild・Force Run・会話のロードのいずれの後でも、上の条件が成り立つ
- 補正は `reconcileSummarySnapshot` を経由させ、個別の場所で独自に補正しない
- この領域を変更したら、テストを追加して実行する。実行結果を変更履歴に書く

### KVキャッシュ

- システムプロンプトと要約より後ろの会話ログは、要約が発火するまで毎ターン同じ先頭から始まるようにする
- 毎ターン先頭がずれる処理（例: 末尾N件の `slice(-N)`）を入れない
- プロンプト構築を変えたら、実機ログの `cache_n` を確認する。要約・検索・世界線切替・Rebuild の直後は再計算が走る（仕様）

### 世界線分離と記憶

- 記憶の想起と重複統合では、アクティブな世界線の外のエピソードを混ぜない
- 異なる `message_id` を持つ記憶同士を統合しない
- 類似度閾値、風化率、ブロック数などの定数は `server.py` に集約されている。値を変えるときは README と変更履歴も更新する

### 会話データのサーバーマスター同期（chats/）

- 会話データ（JSONL）はブラウザの IndexedDB だけでなく、PC・LAN・Tailscale 間で一元化するため `server.py`（`backend/data/chats/`）をマスターとして双方向・起動時同期される
- セッション切り替え時やメッセージ追加・更新時にサーバーへ同期保存される
- オフライン時やサーバー一時切断時も IndexedDB をフォールバックとして保持する
- 会話構造やセッションIDの取り扱いを変更する際は、IndexedDB だけでなく `chats/` ディレクトリ内の JSONL 読み書き互換性を維持すること

## セキュリティ・外部通信

- コアサーバー（5000 / 5001 / 8080番）は `127.0.0.1` にバインドする。外部端末からのアクセスは認証ゲートウェイ（8090番）経由のみに限定する
- 外部通信は、Web検索トグルがONで、ユーザーが明示的に指示したときだけ発生させる。通信先やトリガーを変えたら `docs/external-connections.md` を更新する
- 非GETリクエストの `Origin` / `Referer` 検証、`Host` 検証、セッションIDの検証（`is_safe_session_id` など）を弱めない
- 破壊的な操作にGETを使わない。5000番に静的ファイル配信のワイルドカードルートを足さない
- 8080番のAPIキー保護は、静的ファイルまで401になるため見送り済み（変更履歴 32）

## テスト

- 回帰・セキュリティ: `python tests/run_security_tests.py`（隔離ポート5100、一時ディレクトリで動作）
- 純粋関数の単体テスト: `python tests/test_session_validator.py` / `python tests/test_search_trigger.py`
- 本番データ（`chats`、`chroma_db`、`custom_public`）に対してテストを実行しない。`DEINEI_DATA_DIR`、`DEINEI_CHROMA_DIR`、`DEINEI_CHATS_DIR` などで隔離先を指定する
- 新しい判定ロジックは、`server.py` に依存しない純粋関数として `backend/data/` に置き、`tests/` にテストを書く

## ビルド・配布

- インストーラーによる環境再現性: まっさらな環境（新規マシン・クリーンなディレクトリ）で `installer/setup.ps1` を実行した際、ユーザー個別データ（対話ログ `chats/`、ベクトル記憶 `chroma_db/`、ユーザーが追加したモデル等）以外の全機能・スクリプト・依存パッケージ・UI成果物が再現性をもって復元され、正常に起動できるようにしなければならない。
  - 開発者ローカル環境の固有パス（`Downloads/` や `%TEMP%` など）への依存をコード上に残さない
  - 動作に必要な静的ファイル（アイコンやUIアセット等）は `tools/ui/static/` などの Git 管理下の所定位置に正しく配置する
  - WebUI を改修・ビルドした際は、配布用フォルダ（`backend/llama.cpp-b9873/custom_public`）へ成果物を確実に同期し、コミット対象に含める（インストーラーの即時展開ロジックで古いビルドが上書きされる事故を防ぐため）
- WebUIを修正したら、`backend/llama.cpp-b9873/tools/ui` で `npm run build:deining` を実行する
- ビルド成果物は、実行用サンドボックスの `llama-b9873-bin-.../custom_public` に出力される。これを配布用の `backend/llama.cpp-b9873/custom_public` に同期する（`robocopy ... /MIR`）。Node.js のない一般ユーザーには、この配布用フォルダの内容が届く
- `custom_public` 内のビルド済み `bundle.js` を直接書き換えない。変数名が難読化されているため、実行時エラー（TypeError / ReferenceError）の原因になる
- `start_ai_servers.bat` はGit管理外の生成物。`installer/templates/start_ai_servers.bat` から `setup.ps1` が生成するので、変更はテンプレート側に入れる。`deining_profile.bat` も `setup.ps1` が生成する
- `.bat` と `.ps1` の改行は CRLF（`.gitattributes` で指定済み）。変更しない
- `installer/setup.ps1` は必ず **UTF-8 with BOM**（`0xEF, 0xBB, 0xBF`）で保存する。エディタやAIツール、スクリプト等で上書き保存する際にBOMが脱落しやすいため、編集後は必ずBOMが付与されていることを確認する。BOMがないとWindows標準のPowerShell 5.1がShift-JIS（CP932）と誤認し、全角文字のバイト列が制御記号と衝突してParserError（Missing closing '}' / Unexpected token等）が発生し、インストーラーが起動即クラッシュする
- `.gitignore` の除外対象の下に、起動に必要なファイルを置かない（例: jinjaテンプレートは `backend/templates/`）
- モデル名・ファイル名を変えるときは、README、`setup.ps1`、起動バッチ等の関連ファイルを同じ変更で更新する（ローカルに内部ガイドが存在する場合は併せて更新）

## ライセンスと同梱物

- 本プロジェクトは MIT ライセンスのもとでオープンソースソフトウェア（OSS）として公開される。追加・改修するコードやスクリプトは、MIT ライセンスと互換性のある形式を維持すること
- Live2D Cubism Core、Live2Dモデル、LLMなどのモデル重みは、リポジトリに同梱・再配布しない。インストーラーでの取得か、入手先の案内にとどめる
- 新しい依存ライブラリや素材を追加するときは、ライセンスを確認してクレジットに追記する
- Nominatim と Open-Meteo の利用条件（リクエスト頻度、User-Agent、帰属表示）を守る

## 既知の制限

READMEの「既知の制限（Known Limitations）」に従う。新しい制限を見つけたら、READMEと変更履歴の両方に追記する。
