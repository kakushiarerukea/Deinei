import os, sys, io, shutil
# Windowsコンソールでの絵文字・UTF-8出力を安全に有効化 (ストリームを破棄せず再構成しC拡張クラッシュを防止)
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    except Exception:
        pass

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

# 統合キャッシュ (.cache) の設定: 他ツール (SD/ComfyUI等) の環境と分離し、アンインストール時の一括削除を容易化
_SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT_DIR = os.path.dirname(os.path.dirname(_SERVER_DIR))
_LOCAL_CACHE_ROOT = os.path.join(_PROJECT_ROOT_DIR, ".cache")
os.environ.setdefault("HF_HOME", os.path.join(_LOCAL_CACHE_ROOT, "huggingface"))
os.environ.setdefault("TORCH_HOME", os.path.join(_LOCAL_CACHE_ROOT, "torch"))

try:
    _local_hf_hub = os.path.join(os.environ["HF_HOME"], "hub")
    _global_hf_hub = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
    _ruri_dir_name = "models--cl-nagoya--ruri-v3-310m"
    _local_ruri = os.path.join(_local_hf_hub, _ruri_dir_name)
    _global_ruri = os.path.join(_global_hf_hub, _ruri_dir_name)
    if not os.path.exists(_local_ruri) and os.path.exists(_global_ruri):
        os.makedirs(_local_hf_hub, exist_ok=True)
        shutil.copytree(_global_ruri, _local_ruri, dirs_exist_ok=True)
except Exception:
    pass

from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
import json, uuid, threading, re, logging, time, hashlib, base64, hmac, urllib.parse, ipaddress
from datetime import datetime
import requests as http_requests
from search_trigger import should_trigger_search

# HTTPセッション管理 (コネクションプールによるkeep-alive再利用でTTFTの遅延を防止)
_http_session = http_requests.Session()
_http_adapter = http_requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=50)
_http_session.mount('http://', _http_adapter)
_http_session.mount('https://', _http_adapter)

# Stage 1番兵値（「検索不要」の明示的判定）
STAGE1_NONE = "__STAGE1_NONE__"


# ====== インメモリ検索結果キャッシュ (Flask終了時に自動破棄) ======
_SEARCH_CACHE = {}  # { query_key: {"results": [...], "timestamp": float} }
_CACHE_TTL_SECONDS = 3600  # 1時間有効
_SEARCH_CACHE_MAX_SIZE = 256
_SEARCH_CACHE_LOCK = threading.Lock()

def _prune_search_cache(now_ts: float = None):
    """期限切れまたは上限超過のインメモリ検索キャッシュをパージする。"""
    if now_ts is None:
        now_ts = time.time()
    with _SEARCH_CACHE_LOCK:
        expired_keys = [
            k for k, v in list(_SEARCH_CACHE.items())
            if not isinstance(v, dict) or (now_ts - v.get('timestamp', 0) >= _CACHE_TTL_SECONDS)
        ]
        for k in expired_keys:
            _SEARCH_CACHE.pop(k, None)
        while len(_SEARCH_CACHE) > _SEARCH_CACHE_MAX_SIZE:
            oldest_key = min(_SEARCH_CACHE.keys(), key=lambda k: _SEARCH_CACHE[k].get('timestamp', 0))
            _SEARCH_CACHE.pop(oldest_key, None)

def _get_json_dict(silent: bool = True) -> dict:
    """リクエストJSONを辞書として安全に取得する（配列・文字列・不正JSON時は空辞書を返す）。"""
    data = request.get_json(silent=silent)
    return data if isinstance(data, dict) else {}

# ====== Web検索トグル ======
_SEARCH_ENABLED = False
_SEARCH_TRIGGER_KEYWORDS_JA = [
    '検索して', '調べて', 'ググって', '調査して', '〜を教えて', '〜について教えて', '〜について知りたい',
    '天気予報', '最新ニュース',
]
_SEARCH_TRIGGER_KEYWORDS_EN = [
    'search for', 'look up', 'google it', 'weather forecast', 'latest news', 'tell me about',
]
_SEARCH_TRIGGER_KEYWORDS = _SEARCH_TRIGGER_KEYWORDS_JA + _SEARCH_TRIGGER_KEYWORDS_EN

# ====== 検索デバッグログ設定 ======
# コンソール: INFO以上（1行サマリーのみ）
# ファイル (search_debug.log): DEBUG以上（検索クエリ・取得データの全詳細）
_search_logger = logging.getLogger('deining.search')
_search_logger.setLevel(logging.DEBUG)
_log_dir = os.path.abspath(os.environ.get('DEINEI_DATA_DIR', '').strip() or os.path.dirname(os.path.abspath(__file__)))
os.makedirs(_log_dir, exist_ok=True)
_fh = logging.FileHandler(os.path.join(_log_dir, 'search_debug.log'), encoding='utf-8')
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter('%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
_search_logger.addHandler(_fh)

app = Flask(__name__)
# [S07 fix] 受信ボディ上限 (multipart ボディの無制限ディスクスプール防止。STT 25MB / ZIP 解凍上限 150MB+α をカバー)
app.config['MAX_CONTENT_LENGTH'] = 256 * 1024 * 1024

# ====== ネットワーク・セキュリティ設定の一元管理 ======
# 環境変数 DEINEI_PORT, LLAMA_PORT, ALLOWED_ORIGINS, DEINEI_API_KEY, DEINEI_HOST から動的に設定可能
SERVER_PORT = int(os.environ.get('DEINEI_PORT', 5000))
LLAMA_PORT = int(os.environ.get('LLAMA_PORT', 8080))
LLAMA_SERVER_URL = os.environ.get('LLAMA_SERVER_URL', f'http://127.0.0.1:{LLAMA_PORT}')
DEINEI_API_KEY = os.environ.get('DEINEI_API_KEY', '').strip()
BIND_HOST = os.environ.get('DEINEI_HOST', '127.0.0.1').strip()

# 非ループバックアドレス(0.0.0.0等)へのバインド時は APIキー設定を強制
if BIND_HOST not in ('127.0.0.1', 'localhost', '::1') and not DEINEI_API_KEY:
    sys.exit("[ERROR] 非ループバックアドレス(0.0.0.0やLAN IP等)にバインドする場合は DEINEI_API_KEY の設定が必須です。")

# ALLOWED_ORIGINS の厳格バリデーション (* や null, 空白, 非HTTP/HTTPS は起動時エラー)
_raw_origins = [o.strip() for o in os.environ.get('ALLOWED_ORIGINS', '').split(',') if o.strip()]
_safe_extra_origins = []
for o in _raw_origins:
    if o in ('*', 'null', 'none') or o.startswith('*') or not o.startswith(('http://', 'https://')):
        sys.exit(f"[ERROR] ALLOWED_ORIGINS に不正または危険なオリジン '{o}' が含まれています。ワイルドカード (*)、null、非HTTP/HTTPS は禁止です。")
    _safe_extra_origins.append(o)

ALLOWED_ORIGINS = {
    f'http://localhost:{LLAMA_PORT}',
    f'http://127.0.0.1:{LLAMA_PORT}',
    f'http://localhost:{SERVER_PORT}',
    f'http://127.0.0.1:{SERVER_PORT}',
    *_safe_extra_origins
}

ALLOWED_HOSTS = {
    f'localhost:{SERVER_PORT}',
    f'127.0.0.1:{SERVER_PORT}',
    'localhost',
    '127.0.0.1',
    *[h.split('://')[-1].rstrip('/') for h in _safe_extra_origins]
}

try:
    from flask_cors import CORS
    CORS(app, resources={
        r"/health": {"origins": "*"},
        r"/": {"origins": "*"},
        r"/*": {"origins": list(ALLOWED_ORIGINS)}
    }, allow_headers="*", expose_headers=["X-Deining-Recalled-Memories", "Content-Type"])
    print(f"[OK] CORS 有効化済み (許可オリジン: {', '.join(sorted(ALLOWED_ORIGINS))}, /health は全開放)")
except ImportError:
    pass

@app.before_request
def security_shield():
    # 0. APIキー認証 (DEINEI_API_KEY が設定されている場合は必須検証。OPTIONSプリフライトは除外)
    if DEINEI_API_KEY and request.method != 'OPTIONS':
        auth_header = request.headers.get('Authorization', '')
        api_key_header = request.headers.get('X-API-Key', '')
        token = auth_header[7:] if auth_header.startswith('Bearer ') else api_key_header
        if not token or not hmac.compare_digest(token, DEINEI_API_KEY):
            return jsonify({'error': 'Unauthorized: Invalid or missing API key'}), 401

    # 1. DNS Rebinding 対策: Host ヘッダーの厳格検証
    host = request.host.lower() if request.host else ''
    if host not in ALLOWED_HOSTS:
        return jsonify({'error': 'Invalid Host header'}), 400

    # 2. CSRF 対策: 状態変更メソッド (非GET/HEAD/OPTIONS) に対する Origin / Referer 検査
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('Origin')
        referer = request.headers.get('Referer')

        # Origin: null (sandbox iframe, data: URI, ローカル file:// 等) は無条件で 403 拒絶
        if origin and origin.lower() in ('null', 'none'):
            return jsonify({'error': 'Cross-origin request forbidden (null origin)'}), 403

        if origin:
            # ブラウザのクロスサイトリクエスト (fetch, form POST等) は必ず Origin が付与される
            clean_origin = origin.rstrip('/')
            if clean_origin not in ALLOWED_ORIGINS:
                return jsonify({'error': 'Cross-origin request forbidden'}), 403
        elif referer:
            from urllib.parse import urlparse
            ref_parsed = urlparse(referer)
            ref_origin = f"{ref_parsed.scheme}://{ref_parsed.netloc}"
            if ref_origin not in ALLOWED_ORIGINS:
                return jsonify({'error': 'Cross-origin request forbidden (invalid referer)'}), 403
        else:
            # [意図的仕様] Origin も Referer も存在しない場合:
            # ブラウザからのクロスサイトリクエスト (fetch, form POST等) は必ず Origin または Referer が付与される。
            # いずれも存在しないリクエストはローカルCLI（curl, pythonスクリプト, 自動バッチ等）用途として許可する。
            # ただしブラウザが Sec-Fetch-Site を付与している場合は cross-site を厳格に拒否する。
            sec_fetch_site = request.headers.get('Sec-Fetch-Site')
            if sec_fetch_site and sec_fetch_site not in ('same-origin', 'none'):
                return jsonify({'error': 'Cross-site request forbidden'}), 403

@app.after_request
def add_cors_headers(response):
    req_origin = request.headers.get('Origin')
    req_headers = request.headers.get('Access-Control-Request-Headers')

    if request.path in ('/', '/health') and request.method in ('GET', 'HEAD', 'OPTIONS'):
        response.headers['Access-Control-Allow-Origin'] = req_origin if req_origin else '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, HEAD, OPTIONS'
        return response

    if req_origin and req_origin.rstrip('/') in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = req_origin
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = req_headers if req_headers else '*'
        response.headers['Access-Control-Expose-Headers'] = 'X-Deining-Recalled-Memories, Content-Type'
    return response

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.environ.get('DEINEI_DATA_DIR', '').strip() or BASE_DIR)
HISTORY_FILE = os.path.join(DATA_DIR, 'history.json')
AI_HISTORY_FILE = os.path.join(DATA_DIR, 'ai_history.json')
CHROMA_DB_DIR = os.path.abspath(os.environ.get('DEINEI_CHROMA_DIR', '').strip() or os.path.join(DATA_DIR, 'chroma_db'))

# ====== ChromaDB（長期記憶 / RAG）の初期化 ======
import chromadb
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS
from chromadb.api.types import Documents, Embeddings, EmbeddingFunction

class RuriEmbeddingFunction(EmbeddingFunction):
    def __init__(self):
        self.model = None
        self._load_lock = threading.Lock()
        self._load_failed = False
        self._last_error = None
        if os.environ.get("DEINEI_SKIP_EMBED_LOAD") != "1":
            # バックグラウンドスレッドでロードを開始（/health はロード完了まで 503 を返す）
            self._loader_thread = threading.Thread(target=self._load_model_worker, daemon=True)
            self._loader_thread.start()

    def _load_model_worker(self):
        with self._load_lock:
            if self.model is not None:
                return
            import gc
            import glob
            import os

            cached_snapshots = []
            candidate_hub_dirs = [
                os.path.join(os.environ.get("HF_HOME", os.path.join(_LOCAL_CACHE_ROOT, "huggingface")), "hub", "models--cl-nagoya--ruri-v3-310m", "snapshots"),
                os.path.expanduser("~/.cache/huggingface/hub/models--cl-nagoya--ruri-v3-310m/snapshots"),
            ]
            for hub_dir in candidate_hub_dirs:
                if os.path.isdir(hub_dir):
                    for entry in glob.glob(os.path.join(hub_dir, "*")):
                        if os.path.isdir(entry) and os.path.isfile(os.path.join(entry, "config.json")):
                            cached_snapshots.append(entry)

            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                try:
                    import torch

                    # OpenMPスレッド数を制限し、メモリ断片化とC++アロケータ競合クラッシュ(0xc0000005)を防止
                    try:
                        if hasattr(torch, 'set_num_threads'):
                            torch.set_num_threads(min(4, os.cpu_count() or 4))
                    except Exception:
                        pass

                    if attempt == 1:
                        print("[INFO] cl-nagoya/ruri-v3-310m を RAM (CPU) モードでロード中...", flush=True)
                    else:
                        print(f"[INFO] cl-nagoya/ruri-v3-310m のロードを再試行中 ({attempt}/{max_attempts})...", flush=True)

                    from sentence_transformers import SentenceTransformer
                    if cached_snapshots:
                        local_path = sorted(cached_snapshots)[-1]
                        self.model = SentenceTransformer(local_path, device='cpu')
                    else:
                        try:
                            os.environ["HF_HUB_OFFLINE"] = "1"
                            self.model = SentenceTransformer('cl-nagoya/ruri-v3-310m', device='cpu')
                        except Exception:
                            os.environ.pop("HF_HUB_OFFLINE", None)
                            print("[INFO] 埋め込みモデルをダウンロード中 (初回のみ約1.3GB / 数十秒かかります)...", flush=True)
                            self.model = SentenceTransformer('cl-nagoya/ruri-v3-310m', device='cpu')
                        finally:
                            os.environ.pop("HF_HUB_OFFLINE", None)

                    self._load_failed = False
                    self._last_error = None
                    print("[OK] cl-nagoya/ruri-v3-310m ロード完了", flush=True)
                    return
                except Exception as e:
                    self._last_error = str(e)
                    gc.collect()
                    if attempt < max_attempts:
                        print(f"[WARN] cl-nagoya/ruri-v3-310m ロード一時失敗 (3秒後に再試行): {e}", flush=True)
                        time.sleep(3.0)
                    else:
                        self._load_failed = True
                        print(f"[ERROR] cl-nagoya/ruri-v3-310m のロードに失敗しました: {e}", flush=True)

    def _ensure_loaded(self, timeout: float = 60.0) -> bool:
        """モデルのロード完了を待機する。"""
        if self.model is not None:
            return True
        if hasattr(self, '_loader_thread') and self._loader_thread.is_alive():
            self._loader_thread.join(timeout=timeout)
        if self.model is not None:
            return True
        if self._load_failed:
            return False
        return self.model is not None

    def __call__(self, input: Documents) -> Embeddings:
        if not self._ensure_loaded() or self.model is None:
            print("[WARN] 埋め込みモデル未ロード状態でのリクエストを拒否しました (ゼロベクトル保存防止)", flush=True)
            raise RuntimeError("Embedding model (cl-nagoya/ruri-v3-310m) is not loaded")

        processed_inputs = []
        for text in input:
            if text.startswith('- ') or text.startswith('・') or len(text) > 40:
                processed_inputs.append(f"文章: {text}")
            else:
                processed_inputs.append(f"クエリ: {text}")

        embeddings = self.model.encode(processed_inputs, convert_to_numpy=True)
        return embeddings.tolist() if hasattr(embeddings, 'tolist') else list(embeddings)

try:
    ruri_ef = RuriEmbeddingFunction()
except BaseException as e:
    print(f"[WARN] ruri-v3 のロードに失敗しました (ChromaDB デフォルトを使用します): {e}", flush=True)
    ruri_ef = None

def get_embed_model():
    """エンベッディングモデル（SentenceTransformerインスタンス）を安全に取得"""
    if ruri_ef:
        ruri_ef._ensure_loaded(timeout=10.0)
        if hasattr(ruri_ef, 'model') and ruri_ef.model is not None:
            return ruri_ef.model
    return None

chroma_client = chromadb.PersistentClient(path=CHROMA_DB_DIR)
def get_memory_collection(session_id=None):
    """セッション固有のメモリコレクションを取得（なければ作成）"""
    if not session_id:
        session_id = request.args.get('session_id')
    if not session_id and request.is_json:
        session_id = _get_json_dict(silent=True).get('session_id')
    if not session_id:
        session_id = "default_session"

    safe_id = str(session_id).replace('-', '_').lower()
    name = f"memory_{safe_id}"[:63]
    try:
        return chroma_client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
            embedding_function=ruri_ef
        )
    except Exception as e:
        print(f"[WARN] コレクション取得エラー (フォールバック試行): {e}")
        return chroma_client.get_collection(
            name=name,
            embedding_function=ruri_ef
        )

print(f"[OK] ChromaDB 初期化完了 (保存先: {CHROMA_DB_DIR})")

# ====== インメモリ即席検索リトリーバル (Ephemeral Web Retrieval / 5時間有効) ======
_ephemeral_client = chromadb.EphemeralClient()
_web_ephemeral_collection = _ephemeral_client.get_or_create_collection(
    name="ephemeral_web_retrieval",
    metadata={"hnsw:space": "cosine"},
    embedding_function=ruri_ef
)
_WEB_EPHEMERAL_TTL_SECONDS = 5 * 3600  # 5時間 (18,000秒)
print("[OK] インメモリ即席Webリトリーバル (EphemeralClient) 初期化完了 (TTL: 5時間)")

def _prune_ephemeral_web():
    """期限切れ (expires_ts < now) の即席Webリトリーバルドキュメントを削除"""
    if not _web_ephemeral_collection or _web_ephemeral_collection.count() == 0:
        return
    try:
        now_ts = time.time()
        expired = _web_ephemeral_collection.get(where={"expires_ts": {"$lt": now_ts}})
        if expired and expired.get("ids"):
            _web_ephemeral_collection.delete(ids=expired["ids"])
            print(f"[INFO] [Ephemeral Web Prune] 期限切れ {len(expired['ids'])} 件を削除しました")
    except Exception as e:
        pass

def _register_ephemeral_web_retrieval(query_text, results, session_id='default_session'):
    """取得したWeb検索/気象スニペットをインメモリ即席親子リトリーバルに登録 (TTL: 5時間, チャットセッション単位)"""
    if not results or not _web_ephemeral_collection:
        return
    try:
        from datetime import datetime, timedelta
        now = datetime.now()
        expires_at = now + timedelta(seconds=_WEB_EPHEMERAL_TTL_SECONDS)
        _prune_ephemeral_web()
        now_iso = now.isoformat()
        exp_iso = expires_at.isoformat()
        exp_ts = now.timestamp() + _WEB_EPHEMERAL_TTL_SECONDS
        active_sid = session_id or 'default_session'

        docs_to_add = []
        metas_to_add = []
        ids_to_add = []

        for idx, item in enumerate(results):
            parent_text = item.strip()
            if not parent_text:
                continue

            child_keys = []
            clean_q = _extract_search_keywords(query_text)
            if clean_q:
                child_keys.append(clean_q)

            title_match = re.search(r'【(.*?)】', parent_text)
            if title_match:
                title = title_match.group(1).strip()
                if title:
                    child_keys.append(title)

            bracket_keys = re.findall(r'[「『](.*?)[」』]', parent_text)
            for bk in bracket_keys[:2]:
                if bk.strip() and len(bk.strip()) > 2:
                    child_keys.append(bk.strip())

            unique_keys = list(dict.fromkeys([k for k in child_keys if k]))
            if not unique_keys:
                unique_keys = [clean_q or query_text]

            for k_idx, key in enumerate(unique_keys):
                seed_str = f"{active_sid}_{query_text}_{idx}_{key}_{parent_text[:30]}"
                doc_hash = hashlib.sha256(seed_str.encode('utf-8')).hexdigest()[:16]
                doc_id = f"web_{doc_hash}_{k_idx}"
                meta = {
                    "type": "ephemeral_web",
                    "session_id": active_sid,
                    "query": query_text,
                    "timestamp": now_iso,
                    "expires_at": exp_iso,
                    "expires_ts": exp_ts,
                    "parent_text": parent_text
                }
                docs_to_add.append(key)
                metas_to_add.append(meta)
                ids_to_add.append(doc_id)

        if docs_to_add:
            _web_ephemeral_collection.upsert(
                documents=docs_to_add,
                metadatas=metas_to_add,
                ids=ids_to_add
            )
            print(f"[OK] [Ephemeral Web] 新規検索結果を即席リトリーバルに登録しました (session={active_sid}, {len(docs_to_add)}キー, 有効期限: 5時間)")
    except Exception as e:
        print(f"[WARN] 即席Webリトリーバル登録エラー: {e}")

def _search_ephemeral_web_for_proxy(query_text, session_id='default_session', n_results=6, with_scores=False):
    """インメモリ即席Webリトリーバルから過去5時間以内の検索知識を想起 (類似度 0.85 以上、同一チャットセッション限定)"""
    _prune_ephemeral_web()
    if not _web_ephemeral_collection or _web_ephemeral_collection.count() == 0:
        return []
    try:
        from datetime import datetime
        now = datetime.now()
        active_sid = session_id or 'default_session'

        res = _web_ephemeral_collection.query(
            query_texts=[query_text],
            n_results=min(12, _web_ephemeral_collection.count()),
            where={"session_id": active_sid}
        )
        docs = res['documents'][0] if res['documents'] else []
        dists = res['distances'][0] if res['distances'] else []
        metas = res['metadatas'][0] if res['metadatas'] else []

        print(f"--- 即席Webリトリーバル (5時間有効 / session={active_sid}) 検索結果 ({len(docs)}件候補) ---")

        seen_parents = set()
        candidates = []

        for doc, raw_dist, meta in zip(docs, dists, metas):
            sim = max(0.0, 1.0 - raw_dist)
            parent_text = meta.get('parent_text', '') if meta else ''
            exp_str = meta.get('expires_at', '') if meta else ''
            
            is_expired = False
            if exp_str:
                try:
                    exp_dt = datetime.fromisoformat(exp_str)
                    if now > exp_dt:
                        is_expired = True
                except Exception:
                    pass

            parent_preview = parent_text.replace('\n', ' ')[:50] + '...' if len(parent_text) > 50 else parent_text

            status_str = "[採用候補]"
            if is_expired:
                status_str = "[不採用: 期限切れ (> 5時間)"
            elif raw_dist > 0.150:
                status_str = f"[不採用: 類似度不足 (類似度: {sim:.4f} < 0.85 / 距離: {raw_dist:.4f} > 0.150)"
            elif parent_text in seen_parents:
                status_str = "[不採用: 重複親スニペット]"

            print(f"  [{status_str}] 類似度: {sim:.4f} (距離: {raw_dist:.4f}) | キー: 「{doc}」 -> 親: {parent_preview}")

            if is_expired or raw_dist > 0.150 or not parent_text or parent_text in seen_parents:
                continue

            candidates.append({
                'type': 'web',
                'sim': sim,
                'dist': raw_dist,
                'text': parent_text,
                'raw': parent_text
            })
            seen_parents.add(parent_text)

        candidates.sort(key=lambda x: x['sim'], reverse=True)
        if with_scores:
            return candidates[:n_results]
        recalled = [c['text'] for c in candidates[:n_results]]
        if recalled:
            print(f"[INFO] 最終想起Web知識件数: {len(recalled)}件 (session={active_sid})")
        return recalled
    except Exception as e:
        print(f"[WARN] 即席Webリトリーバル検索エラー: {e}")
        return []

# ====== ヘルスチェック＆ルート ======

@app.route('/')
@app.route('/health')
def health_check():
    if os.environ.get("DEINEI_SKIP_EMBED_LOAD") == "1":
        return jsonify({'status': 'ok', 'service': 'deining-proxy', 'embed_model': 'skipped'})
    if ruri_ef is None or getattr(ruri_ef, '_load_failed', False):
        err_msg = getattr(ruri_ef, '_last_error', None) or 'Embedding model failed to load'
        return jsonify({
            'status': 'error',
            'service': 'deining-proxy',
            'error': err_msg
        }), 500
    if getattr(ruri_ef, 'model', None) is None:
        return jsonify({
            'status': 'loading',
            'service': 'deining-proxy',
            'detail': 'Loading embedding model (cl-nagoya/ruri-v3-310m)'
        }), 503
    return jsonify({'status': 'ok', 'service': 'deining-proxy', 'embed_model': 'ready'})

CHATS_DIR = os.path.abspath(os.environ.get('DEINEI_CHATS_DIR', '').strip() or os.path.join(DATA_DIR, 'chats'))
if not os.path.exists(CHATS_DIR):
    os.makedirs(CHATS_DIR, exist_ok=True)

from session_validator import is_safe_session_id

def get_safe_session_path(session_id: str) -> str | None:
    """パストラバーサルおよびWindows予約名を完全に遮断し、CHATS_DIR配下の絶対パスのみを返す"""
    if not is_safe_session_id(session_id):
        return None
    chats_abs = os.path.abspath(CHATS_DIR)
    target_path = os.path.abspath(os.path.join(chats_abs, f"{session_id}.json"))
    try:
        if os.path.commonpath([chats_abs, target_path]) != chats_abs:
            return None
    except ValueError:
        return None
    return target_path

# ====== 既存履歴のマイグレーション ======
def migrate_legacy_history():
    default_session_path = os.path.join(CHATS_DIR, 'default_session.json')
    if not os.path.exists(default_session_path):
        legacy_history = []
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                    legacy_history = json.load(f)
            except Exception: pass
            
        ai_text = ''
        last_index = 0
        if os.path.exists(AI_HISTORY_FILE):
            try:
                with open(AI_HISTORY_FILE, 'r', encoding='utf-8') as f:
                    ai_data = json.load(f)
                    ai_text = ai_data.get('text', '')
                    last_index = ai_data.get('lastSummarizedIndex', 0)
            except Exception: pass
            
        if legacy_history or ai_text:
            from datetime import datetime
            session_data = {
                "id": "default_session",
                "name": "以前のチャット (Default)",
                "updatedAt": datetime.now().isoformat(),
                "messages": legacy_history, # フロントエンドでツリーに変換させる用
                "aiHistoryText": ai_text,
                "lastSummarizedIndex": last_index
            }
            with open(default_session_path, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, ensure_ascii=False, indent=2)
            
            # バックアップとしてリネーム
            if os.path.exists(HISTORY_FILE):
                bak = HISTORY_FILE + '.bak'
                if os.path.exists(bak):
                    try: os.remove(bak)
                    except Exception: pass
                try: os.rename(HISTORY_FILE, bak)
                except Exception as e: print(f"[WARN] history.json バックアップ失敗: {e}")

            if os.path.exists(AI_HISTORY_FILE):
                bak = AI_HISTORY_FILE + '.bak'
                if os.path.exists(bak):
                    try: os.remove(bak)
                    except Exception: pass
                try: os.rename(AI_HISTORY_FILE, bak)
                except Exception as e: print(f"[WARN] ai_history.txt バックアップ失敗: {e}")
            print("[OK] 既存の history.json を default_session.json にマイグレーションしました。")

migrate_legacy_history()

# ====== セッション管理API ======

@app.route('/api/sessions', methods=['GET'])
def get_sessions():
    sessions = []
    if os.path.exists(CHATS_DIR):
        for f in os.listdir(CHATS_DIR):
            if f.endswith('.json'):
                path = os.path.join(CHATS_DIR, f)
                try:
                    with open(path, 'r', encoding='utf-8') as file:
                        data = json.load(file)
                        sessions.append({
                            "id": data.get("id"),
                            "name": data.get("name", "Unknown Session"),
                            "updatedAt": data.get("updatedAt", ""),
                            "lastModified": data.get("lastModified") or 0,
                            "currNode": data.get("currNode", "")
                        })
                except Exception:
                    pass
    sessions.sort(key=lambda x: x.get('lastModified') or 0, reverse=True)
    return jsonify(sessions)

@app.route('/api/sessions', methods=['POST'])
def create_session():
    data = _get_json_dict(silent=True) or {}
    session_id = data.get('id') or str(uuid.uuid4())
    path = get_safe_session_path(session_id)
    if not path:
        return jsonify({'error': 'Invalid session ID'}), 400

    from datetime import datetime
    now_iso = datetime.now().isoformat()
    now_ms = int(datetime.now().timestamp() * 1000)

    # 既存のメッセージデータがあればそれを保持、なければ空のマップ
    session_data = {
        "id": session_id,
        "name": data.get('name', '新規チャット'),
        "updatedAt": data.get('updatedAt') or now_iso,
        "lastModified": data.get('lastModified') or now_ms,
        "currNode": data.get('currNode', ''),
        "lastSummarizedIndex": data.get('lastSummarizedIndex', 0),
        "aiHistoryText": data.get('aiHistoryText', ''),
        "aiHistoryTextList": data.get('aiHistoryTextList', []),
        "isSlidingActive": data.get('isSlidingActive', False),
        "messages": data.get('messages', {})
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(session_data, f, ensure_ascii=False, indent=2)
    return jsonify(session_data)

@app.route('/api/sessions/<path:session_id>', methods=['GET'])
def get_session(session_id):
    path = get_safe_session_path(session_id)
    if not path:
        return jsonify({"error": "Invalid session ID"}), 400
    if not os.path.exists(path):
        return jsonify({"error": "not found"}), 404
    with open(path, 'r', encoding='utf-8') as f:
        return jsonify(json.load(f))

@app.route('/api/sessions/<path:session_id>', methods=['POST', 'PUT'])
def update_session(session_id):
    path = get_safe_session_path(session_id)
    if not path:
        return jsonify({"error": "Invalid session ID"}), 400
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON"}), 400

    from datetime import datetime
    data['id'] = session_id
    if 'updatedAt' not in data:
        data['updatedAt'] = datetime.now().isoformat()
    if 'lastModified' not in data:
        data['lastModified'] = int(datetime.now().timestamp() * 1000)

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "id": session_id})

@app.route('/api/sessions/<path:session_id>', methods=['DELETE'])
def delete_session(session_id):
    path = get_safe_session_path(session_id)
    if not path:
        return jsonify({"error": "Invalid session ID"}), 400
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass
    # ChromaDBのコレクションも削除
    try:
        safe_id = session_id.replace('-', '_').lower()
        chroma_client.delete_collection(f"memory_{safe_id}"[:63])
        print(f"[INFO] セッション {session_id} のRAGメモリを削除しました")
    except Exception:
        pass
    return jsonify({"ok": True})

# ====== 記憶・要約・類似度定数の一元定義 ======
# 1. 類似度 / 距離閾値
MEMORY_ADD_SIMILARITY_THRESHOLD = 0.08  # /memory/add コサイン距離閾値 (0.08以下 = 実質同一事実として重複判定)
FACT_SEARCH_MAX_DISTANCE = 0.135        # Fact記憶検索 コサイン距離閾値 (0.135以下 = 類似度0.865以上)
EPISODE_SEARCH_MAX_DISTANCE = 0.150     # Episode記憶検索 (親子リトリーバル) コサイン距離閾値 (0.150以下 = 類似度0.85以上)
DEDUP_SIMILARITY_THRESHOLD = 0.92       # /memory/deduplicate コサイン類似度閾値 (0.92以上で統合)

# 2. 風化ペナルティ (created_at基準)
FACT_DECAY_RATE_PER_DAY = 0.02          # Fact記憶 1日あたりの距離加算ペナルティ
FACT_DECAY_MAX_PENALTY = 1.0            # Fact記憶 最大風化ペナルティ
EPISODE_DECAY_RATE_PER_DAY = 0.002      # Episode記憶 1日あたりの距離加算ペナルティ
EPISODE_DECAY_MAX_PENALTY = 0.1         # Episode記憶 最大風化ペナルティ

# 3. 要約ブロック定数 (UI側 stores/chat.svelte.ts のスライディング制御と整合)
MAX_SUMMARY_BLOCKS = 16                 # 会話履歴に保持する最大要約ブロック数
FORGET_BLOCKS_COUNT = 2                 # 上限到達時に押し出されるブロック数

def normalize_fact_text(text: str) -> str:
    """日本語文脈において、英語の主語 (Self / User) が漏れ出た場合に '自分' / 'ユーザー' へ正規化する"""
    if not text:
        return text
    # 日本語（ひらがな、カタカナ、漢字）が含まれているか判定
    if not re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', text):
        return text
    # 単語単位の Self / User を正規化 (myself や username 等の英単語内の一致は除外)
    res = re.sub(r'(?i)(?<![a-zA-Z0-9_])self(?![a-zA-Z0-9_])', '自分', text)
    res = re.sub(r'(?i)(?<![a-zA-Z0-9_])user(?![a-zA-Z0-9_])', 'ユーザー', res)
    return res

# ====== 長期記憶（RAG）API ======

@app.route('/memory/add', methods=['POST'])
def add_memory():
    """記憶を追加・類似記憶の重複判定を行う。
    リクエストボディ: { "texts": ["事実1", "事実2", ...], "message_id": "...", "active_message_ids": [...] }
    active_message_ids が渡された場合のみ、現在の世界線内または message_id なしの既存記憶と重複判定を行う。
    重複時はテキストや登録日時は上書きせず、updated_at のみ更新する。
    """
    data = _get_json_dict(silent=True)
    texts = data.get('texts', [])
    if not isinstance(texts, list):
        texts = [texts] if isinstance(texts, str) else []
    if not texts and isinstance(data.get('text'), str):
        texts = [data['text']]

    if not texts:
        return jsonify({'ok': True, 'added': 0, 'updated': 0})

    if ruri_ef is not None and hasattr(ruri_ef, '_ensure_loaded') and not ruri_ef._ensure_loaded():
        return jsonify({'error': 'Embedding model is not ready'}), 503

    session_id = request.args.get('session_id') or data.get('session_id') or 'default_session'
    message_id = request.args.get('message_id') or data.get('message_id')
    active_message_ids = data.get('active_message_ids')
    collection = get_memory_collection(session_id)
    from datetime import datetime

    added_count = 0
    updated_count = 0

    active_ids_set = set(str(mid) for mid in active_message_ids) if isinstance(active_message_ids, list) and active_message_ids else None

    for raw_t in texts:
        if not isinstance(raw_t, str):
            continue
        t = normalize_fact_text(raw_t.strip())
        if not t:
            continue

        existing_match_id = None
        existing_doc = None
        existing_meta = None
        min_dist = 1.0

        # active_message_ids が提供されている場合のみ重複判定を行う（type='fact' のみを対象とし parent_child 子フレーズを除外）
        if active_ids_set is not None and collection.count() > 0:
            try:
                res = collection.query(
                    query_texts=[t],
                    n_results=min(5, collection.count()),
                    where={"$and": [{"channel_id": "web_ui"}, {"type": "fact"}]}
                )
                if res and res.get('documents') and res['documents'][0]:
                    docs = res['documents'][0]
                    distances = res['distances'][0]
                    metadatas = res['metadatas'][0]
                    ids = res['ids'][0]

                    for cand_id, cand_doc, cand_dist, cand_meta in zip(ids, docs, distances, metadatas):
                        cand_meta = cand_meta or {}
                        if cand_meta.get('type') == 'parent_child':
                            continue
                        cand_msg_id = cand_meta.get('message_id')
                        # 条件: 距離 <= 0.08、かつ message_id が無い、または active_message_ids に含まれる
                        is_worldline_ok = (not cand_msg_id) or (str(cand_msg_id) in active_ids_set)
                        if cand_dist <= MEMORY_ADD_SIMILARITY_THRESHOLD and is_worldline_ok:
                            if cand_dist < min_dist:
                                min_dist = cand_dist
                                existing_match_id = cand_id
                                existing_doc = cand_doc
                                existing_meta = cand_meta
            except Exception as e:
                print(f"[WARN] 類似記憶チェック中にエラー: {e}")

        now_iso = datetime.now().isoformat()

        try:
            if existing_match_id:
                # 重複時は既存のテキスト・message_id・登録日時を上書きせず、updated_at のみ更新
                updated_meta = dict(existing_meta) if existing_meta else {}
                updated_meta['updated_at'] = now_iso
                if 'created_at' not in updated_meta:
                    updated_meta['created_at'] = updated_meta.get('timestamp') or now_iso
                collection.update(
                    ids=[existing_match_id],
                    metadatas=[updated_meta]
                )
                print(f"[INFO] 類似記憶の更新日時を更新しました (距離: {min_dist:.3f}): 「{existing_doc}」")
                updated_count += 1
            else:
                # 新規の事実記憶として追加 (created_at は不変、timestamp は後方互換として保持)
                new_id = str(uuid.uuid4())
                meta_dict = {
                    "timestamp": now_iso,
                    "created_at": now_iso,
                    "updated_at": now_iso,
                    "type": "fact",
                    "channel_id": "web_ui"
                }
                if message_id:
                    meta_dict["message_id"] = str(message_id)

                collection.add(
                    ids=[new_id],
                    documents=[t],
                    metadatas=[meta_dict]
                )
                print(f"[INFO] 新規記憶を追加しました (session: {session_id}, msg_id: {message_id}): 「{t}」")
                added_count += 1
        except RuntimeError as emb_err:
            print(f"[WARN] 記憶追加時の埋め込みエラー: {emb_err}")
            return jsonify({'error': str(emb_err)}), 503

    return jsonify({'ok': True, 'added': added_count, 'updated': updated_count})

def safe_sentence_truncate(text, max_len=500):
    """文の途中でぶった切られないよう、max_len 付近の直前の句点（。）や改行（\n）で安全に切り詰める。"""
    if not text or len(text) <= max_len:
        return text
    
    truncated = text[:max_len]
    last_period = max(truncated.rfind('。'), truncated.rfind('\n'), truncated.rfind('！'), truncated.rfind('？'))
    if last_period > int(max_len * 0.4):
        return truncated[:last_period + 1].strip()
    return truncated.strip() + "…"

@app.route('/memory/add_parent_child', methods=['POST', 'OPTIONS'])
def add_parent_child_memory():
    """親子リトリーバル用エピソード記憶の登録（冪等・位置ベースID・Upsert）
    リクエストボディ:
    {
        "session_id": "conv-1",
        "message_id": "msg-30",
        "parent_text": "エピソード全文...",
        "child_texts": ["フレーズ1", "フレーズ2", ...],
        "channel_id": "web_ui"
    }
    """
    if request.method == 'OPTIONS':
        return '', 204

    data = _get_json_dict(silent=True)
    session_id = data.get('session_id') or request.args.get('session_id') or 'default_session'
    message_id = data.get('message_id') or request.args.get('message_id') or ''
    parent_text = data.get('parent_text') or ''
    child_texts = data.get('child_texts') or []
    channel_id = data.get('channel_id') or 'web_ui'

    if not isinstance(parent_text, str) or not isinstance(child_texts, list) or not parent_text or not child_texts:
        return jsonify({'error': 'parent_text and child_texts required'}), 400

    if ruri_ef is not None and hasattr(ruri_ef, '_ensure_loaded') and not ruri_ef._ensure_loaded():
        return jsonify({'error': 'Embedding model is not ready'}), 503

    collection = get_memory_collection(session_id)
    now_iso = datetime.now().isoformat()
    clean_parent = safe_sentence_truncate(parent_text.strip(), 600)
    clean_msg_id = str(message_id).strip()
    parent_hash = hashlib.sha256(clean_parent.encode('utf-8')).hexdigest()[:16]

    # 1. 同一エピソード（同一 message_id かつ同一 parent_text）の古い残骸のみを安全に削除試行
    # （16ブロック到達時に同一 message_id で先頭2ブロックが連続登録される際、1ブロック目を削除しないため）
    if clean_msg_id:
        try:
            collection.delete(where={
                "$and": [
                    {"session_id": str(session_id)},
                    {"message_id": clean_msg_id},
                    {"parent_text": clean_parent}
                ]
            })
        except Exception as del_err:
            print(f"[INFO] [ParentChild Pre-Delete Notice]: {del_err}")

    # 2. 位置＋親テキストハッシュベース決定論的 ID による upsert
    # 同一 message_id に複数ブロック（先頭1, 2ブロック）が紐づく場合でも別エピソード間のID衝突を防ぐ
    id_seed = f"{clean_msg_id}_{parent_hash}" if clean_msg_id else f"anon_{parent_hash}"

    ids = []
    docs = []
    metas = []

    for idx, child in enumerate(child_texts):
        if not isinstance(child, str):
            continue
        c_text = child.strip().lstrip('-').strip()
        if not c_text:
            continue
        doc_id = hashlib.sha256(f"pc_{session_id}_{id_seed}_{idx}".encode()).hexdigest()[:32]
        ids.append(doc_id)
        docs.append(c_text)
        metas.append({
            "type": "parent_child",
            "session_id": str(session_id),
            "message_id": clean_msg_id,
            "parent_text": clean_parent,
            "timestamp": now_iso,
            "created_at": now_iso,
            "updated_at": now_iso,
            "last_accessed": now_iso,
            "channel_id": str(channel_id)
        })

    if ids:
        try:
            collection.upsert(ids=ids, documents=docs, metadatas=metas)
            print(f"[INFO] [Parent-Child Saved] Session: {session_id}, Msg: {clean_msg_id or id_seed}, Chunks: {len(ids)}")
        except RuntimeError as emb_err:
            print(f"[WARN] Parent-Child 登録時の埋め込みエラー: {emb_err}")
            return jsonify({'error': str(emb_err)}), 503

    return jsonify({'ok': True, 'count': len(ids)})

@app.route('/memory/prune', methods=['POST', 'OPTIONS'])
def prune_memories():
    """古い・長期間参照されていない記憶を物理削除（LRU Pruning）"""
    if request.method == 'OPTIONS':
        return '', 204
    data = _get_json_dict(silent=True)
    session_id = data.get('session_id') or request.args.get('session_id')
    try:
        max_age_days = float(data.get('max_age_days', 180))
    except (ValueError, TypeError):
        max_age_days = 180.0

    collection = get_memory_collection(session_id)
    if collection.count() == 0:
        return jsonify({'ok': True, 'deleted': 0})

    try:
        res = collection.get(where={"channel_id": "web_ui"})
        if not res or not res.get('ids'):
            return jsonify({'ok': True, 'deleted': 0})

        now = datetime.now()
        deleted_ids = []

        for m_id, meta in zip(res['ids'], res['metadatas']):
            if not meta:
                continue
            access_time_str = (
                meta.get('last_accessed')
                or meta.get('updated_at')
                or meta.get('created_at')
                or meta.get('timestamp')
            )
            if access_time_str:
                try:
                    access_time = datetime.fromisoformat(access_time_str)
                    age_days = (now - access_time).total_seconds() / 86400.0
                    if age_days > max_age_days:
                        deleted_ids.append(m_id)
                except Exception:
                    pass

        if deleted_ids:
            collection.delete(ids=deleted_ids)
            print(f"[INFO] [PRUNE] {len(deleted_ids)} 件の長期未参照記憶を物理削除しました (閾値: {max_age_days}日)")

        return jsonify({'ok': True, 'deleted': len(deleted_ids), 'remaining': collection.count()})
    except Exception as e:
        print(f"[WARN] 記憶プルーニング中にエラー: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/memory/search', methods=['POST'])
def search_memory():
    """記憶を検索する。
    リクエストボディ: { "query": "検索テキスト", "n_results": 3 }
    """
    data = _get_json_dict(silent=True)
    query = data.get('query', '')
    if not isinstance(query, str):
        query = ''
    try:
        n_results = max(1, min(50, int(data.get('n_results', 3))))
    except (ValueError, TypeError):
        n_results = 3
    session_id = data.get('session_id') or request.args.get('session_id')

    if not query:
        return jsonify({'memories': []})

    collection = get_memory_collection(session_id)
    if collection.count() == 0:
        return jsonify({'memories': []})

    results = collection.query(
        query_texts=[query],
        n_results=min(max(10, n_results), collection.count()),
        where={"$and": [{"channel_id": "web_ui"}, {"type": "fact"}]}
    )

    memories = results['documents'][0] if results['documents'] else []
    distances = results['distances'][0] if results['distances'] else []
    metadatas = results['metadatas'][0] if results['metadatas'] else []

    # 関連度が低すぎるもの（元のコサイン距離 > FACT_SEARCH_MAX_DISTANCE）はフィルタリングしつつ、時間による風化をペナルティとして加算
    from datetime import datetime
    results_list = []
    
    for mem, dist, meta in zip(memories, distances, metadatas):
        if (meta or {}).get('type') == 'parent_child':
            continue
        if dist > FACT_SEARCH_MAX_DISTANCE:
            continue
            
        created_at_str = meta.get('created_at') or meta.get('timestamp', '') if meta else ''
        adjusted_dist = dist
        
        if created_at_str:
            try:
                mem_time = datetime.fromisoformat(created_at_str)
                diff = datetime.now() - mem_time
                days = diff.total_seconds() / 86400.0
                penalty = min(FACT_DECAY_MAX_PENALTY, max(0.0, days * FACT_DECAY_RATE_PER_DAY))
                adjusted_dist += penalty
            except Exception:
                pass
                
        results_list.append((mem, adjusted_dist, meta, created_at_str))

    # 風化ペナルティを加味した距離で再ソート
    results_list.sort(key=lambda x: x[1])
    # 上位 n_results 件を取得
    results_list = results_list[:n_results]

    filtered = []
    for mem, adj_dist, meta, created_at_str in results_list:
        if created_at_str:
            ts_str = created_at_str.replace('T', ' ').split('.')[0]
            filtered.append(f"[{ts_str}] {mem}")
        else:
            filtered.append(mem)

    print(f"[INFO] 記憶検索: \"{query[:30]}...\" → {len(filtered)} 件ヒット (元候補: {len(memories)}件)")
    return jsonify({'memories': filtered})

@app.route('/memory', methods=['POST', 'DELETE', 'OPTIONS'])
@app.route('/memory/clear', methods=['POST', 'DELETE', 'OPTIONS'])
def delete_memory():
    """WebUI用の長期記憶を削除する。"""
    if request.method == 'OPTIONS':
        return '', 204
    try:
        data = _get_json_dict(silent=True)
        session_id = data.get('session_id') or request.args.get('session_id')
        collection = get_memory_collection(session_id)
        collection.delete(where={"channel_id": "web_ui"})
        print(f"[INFO] 長期記憶（Web UI分 session={session_id}）を削除しました")
    except Exception as e:
        print(f"[WARN] 記憶の削除中にエラー: {e}")
    return jsonify({'ok': True})

@app.route('/memory/deduplicate', methods=['POST', 'OPTIONS'])
def deduplicate_memories():
    """既存の全記憶の中でコサイン類似度が著しく高いもの同士を一括統合し、ゴミを排除する。"""
    if request.method == 'OPTIONS':
        return '', 204
    try:
        session_id = request.args.get('session_id')
        collection = get_memory_collection(session_id)
        if collection.count() == 0:
            return jsonify({'ok': True, 'merged': 0, 'total_remaining': 0})

        res = collection.get(where={"$and": [{"channel_id": "web_ui"}, {"type": "fact"}]})
        if not res or not res.get('documents'):
            return jsonify({'ok': True, 'merged': 0, 'total_remaining': 0})

        docs = res['documents']
        ids = res['ids']
        metas = res['metadatas']

        # タイムスタンプ順にソート（古い記憶から順に見て、新しい記憶で統合上書き）
        items = [
            (i_id, i_doc, i_meta)
            for i_id, i_doc, i_meta in zip(ids, docs, metas)
            if (i_meta or {}).get('type') != 'parent_child'
        ]
        items.sort(key=lambda x: x[2].get('timestamp', '') if x[2] else '')

        from sentence_transformers import SentenceTransformer, util
        query_model = get_embed_model()
        if not query_model:
            print("[WARN] エンベッディングモデルがロードされていないため、重複統合をスキップします。")
            return jsonify({'ok': True, 'merged': 0, 'total_remaining': len(docs), 'message': 'Embedding model not available'})

        merged_count = 0
        deleted_ids = set()
        merged_details = []

        for i in range(len(items)):
            id1, doc1, meta1 = items[i]
            if id1 in deleted_ids:
                continue

            emb1 = query_model.encode(doc1, convert_to_tensor=True)

            for j in range(i + 1, len(items)):
                id2, doc2, meta2 = items[j]
                if id2 in deleted_ids:
                    continue

                # 世界線をまたぐ統合の禁止（異なる message_id 同士の統合は行わない）
                mid1 = (meta1.get('message_id') or None) if meta1 else None
                mid2 = (meta2.get('message_id') or None) if meta2 else None
                if mid1 != mid2:
                    continue

                emb2 = query_model.encode(doc2, convert_to_tensor=True)
                cosine_sim = float(util.cos_sim(emb1, emb2)[0][0])

                # 類似度閾値以上（実質的に同義・表現違いのみ）
                if cosine_sim >= DEDUP_SIMILARITY_THRESHOLD:
                    # id1 (古い方) を削除し、id2 (新しい方) に統合
                    deleted_ids.add(id1)
                    merged_count += 1
                    merged_details.append({
                        "deleted": doc1,
                        "kept": doc2,
                        "similarity": round(cosine_sim, 3)
                    })
                    print(f"[INFO] 既存記憶を重複削除統合しました (類似度: {cosine_sim:.2f}): 「{doc1}」 -> 「{doc2}」へ集約")
                    break

        if deleted_ids:
            collection.delete(ids=list(deleted_ids))

        remaining = collection.count()
        print(f"[INFO] 記憶の重複クリーンアップ完了: {merged_count} 件統合削除、残存記憶: {remaining} 件")
        return jsonify({'ok': True, 'merged': merged_count, 'total_remaining': remaining, 'merged_details': merged_details})
    except Exception as e:
        print(f"[WARN] 記憶重複整理中にエラー: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/memory/stats', methods=['GET'])
def memory_stats():
    """長期記憶の統計情報を取得する。"""
    session_id = request.args.get('session_id')
    collection = get_memory_collection(session_id)
    count = collection.count()
    return jsonify({'count': count})

@app.route('/memory/list', methods=['GET', 'POST'])
def list_memory():
    """すべてのWebUI用記憶を取得する。active_message_ids が指定されている場合は現在世界線の記憶のみにフィルタする。"""
    try:
        session_id = request.args.get('session_id')
        collection = get_memory_collection(session_id)
        # countが0の場合はエラーになるため回避
        if collection.count() == 0:
            return jsonify({'memories': []})
            
        results = collection.get(
            where={"channel_id": "web_ui"}
        )
        
        # 世界線フィルタのIDセット取得
        active_ids_raw = request.args.get('active_message_ids')
        if not active_ids_raw and request.is_json:
            data = _get_json_dict(silent=True)
            active_ids_raw = data.get('active_message_ids')
            
        path_id_set = None
        if active_ids_raw:
            if isinstance(active_ids_raw, str):
                path_id_set = set(active_ids_raw.split(','))
            elif isinstance(active_ids_raw, list):
                path_id_set = set(active_ids_raw)

        memories = []
        if results and results.get('ids'):
            for i in range(len(results['ids'])):
                meta = results['metadatas'][i] or {}
                # 世界線フィルタ: message_id がメタデータにあり、指定パスに含まれない場合は除外
                if path_id_set is not None:
                    mem_msg_id = meta.get('message_id')
                    if mem_msg_id and mem_msg_id not in path_id_set:
                        continue
                        
                memories.append({
                    'id': results['ids'][i],
                    'text': normalize_fact_text(results['documents'][i]),
                    'metadata': meta
                })
        # 最新順に並び替え
        memories.sort(key=lambda x: x['metadata'].get('timestamp', ''), reverse=True)
        return jsonify({'memories': memories})
    except Exception as e:
        print(f"[WARN] 記憶一覧の取得中にエラー: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/memory/update', methods=['PUT', 'OPTIONS'])
def update_memory():
    """特定の記憶を更新する。"""
    if request.method == 'OPTIONS':
        return '', 204

    data = _get_json_dict(silent=True)
    mem_id = data.get('id')
    new_text = data.get('text')
    
    if not mem_id or not new_text:
        return jsonify({'error': 'id and text are required'}), 400
        
    try:
        collection = get_memory_collection()
        
        # 既存のメタデータを維持するためにまず取得
        existing = collection.get(ids=[mem_id])
        if not existing or not existing['ids']:
            return jsonify({'error': 'Memory not found'}), 404
            
        metadata = existing['metadatas'][0]
        from datetime import datetime
        metadata['updated_at'] = datetime.now().isoformat()
        
        collection.update(
            ids=[mem_id],
            documents=[new_text],
            metadatas=[metadata]
        )
        return jsonify({'ok': True})
    except Exception as e:
        print(f"[WARN] 記憶の更新中にエラー: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/memory/<mem_id>', methods=['DELETE', 'OPTIONS'])
def delete_specific_memory(mem_id):
    """特定の記憶を削除する。同文の重複データおよび全コレクション横断で削除する。"""
    if request.method == 'OPTIONS':
        return '', 204

    try:
        session_id = request.args.get('session_id')
        print(f"[INFO] [DELETE REQUEST] Target mem_id: '{mem_id}', session_id: '{session_id}'")
        
        # まず対象セッション（またはデフォルト）のコレクションを取得
        primary_coll = get_memory_collection(session_id)
        
        target_doc = None
        # 削除前に対象ドキュメントのテキストを取得しておく
        try:
            existing = primary_coll.get(ids=[mem_id])
            if existing and existing.get('documents') and len(existing['documents']) > 0:
                target_doc = existing['documents'][0]
                print(f"[INFO] [TARGET DOC FOUND]: '{target_doc}'")
        except Exception as e:
            print(f"[WARN] 既存記憶テキスト取得時のエラー: {e}")

        # 全コレクションを一覧取得して、対象IDまたは同文ドキュメントを持つコレクションから全消去
        collections_to_search = [primary_coll]
        try:
            all_colls = chroma_client.list_collections()
            for c_info in all_colls:
                c_name = c_info.name if hasattr(c_info, 'name') else str(c_info)
                if c_name.startswith('memory_'):
                    coll = chroma_client.get_collection(name=c_name, embedding_function=ruri_ef)
                    if coll and coll.name != primary_coll.name:
                        collections_to_search.append(coll)
        except Exception as coll_err:
            print(f"[WARN] 全コレクション一覧取得時の警告: {coll_err}")

        deleted_count = 0
        for coll in collections_to_search:
            try:
                # 1. IDで削除
                coll.delete(ids=[mem_id])
                deleted_count += 1
            except Exception:
                pass

            # 2. 同一テキストを持つデータも削除
            if target_doc:
                try:
                    dup_res = coll.get(where={"channel_id": "web_ui"})
                    if dup_res and dup_res.get('ids') and dup_res.get('documents'):
                        dup_ids = [
                            dup_res['ids'][i] 
                            for i in range(len(dup_res['ids'])) 
                            if dup_res['documents'][i] == target_doc
                        ]
                        if dup_ids:
                            coll.delete(ids=dup_ids)
                            print(f"[INFO] [{coll.name}] 同一内容の記憶 {len(dup_ids)} 件を一括全削除しました")
                except Exception:
                    pass

        print(f"[INFO] 個別記憶 (ID: {mem_id}) を全コレクションから削除完了しました")
        return jsonify({'ok': True})
    except Exception as e:
        print(f"[WARN] 個別記憶の削除中にエラー: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/memory/clear_all', methods=['DELETE', 'POST', 'OPTIONS'])
def clear_all_memories():
    """WebUI用の全記憶をすべてのコレクションから完全消去する。"""
    if request.method == 'OPTIONS':
        return '', 204
    try:
        session_id = request.args.get('session_id')
        primary_coll = get_memory_collection(session_id)
        
        collections_to_clear = [primary_coll]
        try:
            all_colls = chroma_client.list_collections()
            for c_info in all_colls:
                c_name = c_info.name if hasattr(c_info, 'name') else str(c_info)
                if c_name.startswith('memory_'):
                    coll = chroma_client.get_collection(name=c_name, embedding_function=ruri_ef)
                    if coll and coll.name != primary_coll.name:
                        collections_to_clear.append(coll)
        except Exception:
            pass

        total_deleted = 0
        for coll in collections_to_clear:
            try:
                if coll.count() > 0:
                    res = coll.get(where={"channel_id": "web_ui"})
                    if res and res.get('ids'):
                        coll.delete(ids=res['ids'])
                        total_deleted += len(res['ids'])
            except Exception:
                pass

        print(f"[INFO] [CLEAR ALL] 全コレクションから Web UI 用記憶 {total_deleted} 件を完全削除しました")
        return jsonify({'ok': True, 'total_deleted': total_deleted})
    except Exception as e:
        print(f"[WARN] 全記憶クリア中にエラー: {e}")
        return jsonify({'error': str(e)}), 500

# ====== Web検索 API ======

@app.route('/search', methods=['POST'])
def web_search():
    """Web検索を実行する。
    リクエストボディ: { "query": "検索テキスト" }
    """
    if not _SEARCH_ENABLED:
        return jsonify({'error': 'Web検索機能は無効化されています (トグルOFF)'}), 403
    data = _get_json_dict(silent=True)
    query = data.get('query', '')
    if not isinstance(query, str):
        query = ''

    if not query:
        return jsonify({'results': []})

    try:
        results = DDGS().text(query, max_results=3, region='jp-jp')
        formatted = []
        for r in results:
            formatted.append(f"Title: {r.get('title', '')}\nSnippet: {r.get('body', '')}\nURL: {r.get('href', '')}")

        print(f"[INFO] 検索実行: \"{query}\" → {len(formatted)} 件取得")
        return jsonify({'results': formatted})
    except Exception as e:
        print(f"[WARN] 検索中にエラー: {e}")
        return jsonify({'results': [], 'error': str(e)}), 500

# ====== 泥濘統合: llama-server プロキシ (RAG/Web検索 注入付き) ======

def get_time_difference_str(timestamp_str):
    try:
        from datetime import datetime
        saved_time = datetime.fromisoformat(timestamp_str)
        now = datetime.now()
        diff = now - saved_time
        
        seconds = diff.total_seconds()
        if seconds < 60:
            return "たった今"
        minutes = seconds / 60
        if minutes < 60:
            return f"{int(minutes)}分前"
        hours = minutes / 60
        if hours < 24:
            return f"{int(hours)}時間前"
        days = hours / 24
        if days < 30:
            return f"{int(days)}日前"
        months = days / 30
        if months < 12:
            return f"{int(months)}ヶ月前"
        years = months / 12
        return f"{int(years)}年前"
    except Exception as e:
        print(f"Time calculation error: {e}")
        return "過去"

def _search_memory_for_proxy(query_text, session_id='default_session', active_message_ids=None, channel_id='web_ui', n_results=5, with_scores=False):
    """プロキシ用: ChromaDBからFact記憶とEpisode記憶（親子リトリーバル）を独立検索する（Dual-Query RAG）"""
    try:
        collection = get_memory_collection(session_id)
        if collection.count() == 0:
            return ([], [])
        
        from datetime import datetime
        path_id_set = set(active_message_ids) if active_message_ids else None
        now = datetime.now()

        # =========================================================================
        # 1. Fact 記憶の検索 (type != parent_child または type == fact)
        # =========================================================================
        fact_memories = []
        fact_items = []
        try:
            fact_res = collection.query(
                query_texts=[query_text],
                n_results=min(12, max(1, collection.count())),
                where={"$and": [{"channel_id": channel_id}, {"type": "fact"}]}
            )
            f_docs = fact_res['documents'][0] if fact_res['documents'] else []
            f_dists = fact_res['distances'][0] if fact_res['distances'] else []
            f_metas = fact_res['metadatas'][0] if fact_res['metadatas'] else []

            print(f"\n{'='*25} RAG 記憶検索デバッグ {'='*25}")
            print(f"[INFO] クエリ: 「{query_text}」 (コレクション総数: {collection.count()}件)")
            if f_docs:
                print(f"--- Fact 記憶 検索結果 ({len(f_docs)}件候補 / 閾値: 類似度 >= 0.865) ---")
                for doc, dist, meta in zip(f_docs, f_dists, f_metas):
                    sim = max(0.0, 1.0 - dist)
                    is_worldline_ok = True
                    if meta and path_id_set is not None:
                        mem_msg_id = meta.get('message_id')
                        if mem_msg_id and mem_msg_id not in path_id_set:
                            is_worldline_ok = False
                    status_str = "[採用]" if (dist <= FACT_SEARCH_MAX_DISTANCE and is_worldline_ok) else ("[不採用: 世界線外" if not is_worldline_ok else f"[不採用: 類似度不足 (類似度: {sim:.4f} < 0.865)")
                    print(f"  [{status_str}] 類似度: {sim:.4f} (距離: {dist:.4f}) | 内容: {doc}")

            f_candidates = []
            for mem, dist, meta in zip(f_docs, f_dists, f_metas):
                if dist > FACT_SEARCH_MAX_DISTANCE:
                    continue
                # 世界線フィルタ: 別ブランチや別世界線のFactは厳格に除外
                if meta and path_id_set is not None:
                    mem_msg_id = meta.get('message_id')
                    if mem_msg_id and mem_msg_id not in path_id_set:
                        continue

                created_at_str = meta.get('created_at') or meta.get('timestamp', '') if meta else ''
                adjusted_dist = dist
                if created_at_str:
                    try:
                        mem_time = datetime.fromisoformat(created_at_str)
                        days = (now - mem_time).total_seconds() / 86400.0
                        penalty = min(FACT_DECAY_MAX_PENALTY, max(0.0, days * FACT_DECAY_RATE_PER_DAY))
                        adjusted_dist += penalty
                    except Exception:
                        pass
                f_candidates.append((mem, adjusted_dist, created_at_str))

            f_candidates.sort(key=lambda x: x[1])
            f_candidates = f_candidates[:n_results]

            for mem, adj_dist, created_at_str in f_candidates:
                clean_mem = normalize_fact_text(mem.strip().lstrip('-').strip())
                sim = max(0.0, 1.0 - adj_dist)
                if created_at_str:
                    time_diff = get_time_difference_str(created_at_str)
                    text_fmt = f"- [{time_diff}] {clean_mem}"
                else:
                    text_fmt = f"- [過去] {clean_mem}"
                fact_memories.append(text_fmt)
                fact_items.append({
                    'type': 'fact',
                    'sim': sim,
                    'dist': adj_dist,
                    'text': text_fmt,
                    'raw': clean_mem
                })
        except Exception as fe:
            print(f"[WARN] Fact記憶検索警告: {fe}")

        # =========================================================================
        # 2. Episode 記憶の検索 (type == parent_child, 親子リトリーバル)
        # =========================================================================
        episode_memories = []
        try:
            ep_res = collection.query(
                query_texts=[query_text],
                n_results=min(12, max(1, collection.count())),
                where={"$and": [{"channel_id": channel_id}, {"type": "parent_child"}]}
            )
            e_docs = ep_res['documents'][0] if ep_res['documents'] else []
            e_dists = ep_res['distances'][0] if ep_res['distances'] else []
            e_metas = ep_res['metadatas'][0] if ep_res['metadatas'] else []
            e_ids = ep_res['ids'][0] if ep_res['ids'] else []

            print(f"--- 親子リトリーバル (Episode) 検索結果 ({len(e_docs)}件候補) ---")
            if not e_docs:
                print("  (親子リトリーバル登録データなし)")

            seen_parents = set()
            ep_candidates = []

            for doc_id, doc, raw_dist, meta in zip(e_ids, e_docs, e_dists, e_metas):
                sim = max(0.0, 1.0 - raw_dist)
                parent_text = meta.get('parent_text', '') if meta else ''
                parent_preview = parent_text.replace('\n', ' ')[:50] + '...' if len(parent_text) > 50 else parent_text

                is_worldline_ok = True
                mem_msg_id = meta.get('message_id') if meta else None
                # 世界線判定: 現在の世界線パスに含まれていない別ブランチのエピソードは厳格に除外
                if meta and path_id_set is not None:
                    if mem_msg_id and mem_msg_id not in path_id_set:
                        is_worldline_ok = False

                # 判定（類似度 0.85 以上 = 距離 <= EPISODE_SEARCH_MAX_DISTANCE）
                status_str = "[採用]"
                if raw_dist > EPISODE_SEARCH_MAX_DISTANCE:
                    status_str = f"[不採用: 類似度不足 (類似度: {sim:.4f} < 0.85 / 距離: {raw_dist:.4f} > {EPISODE_SEARCH_MAX_DISTANCE})"
                elif not is_worldline_ok:
                    status_str = f"[不採用: 世界線外 ({mem_msg_id})"
                elif parent_text in seen_parents:
                    status_str = "[不採用: 重複親エピソード]"

                print(f"  [{status_str}] 類似度: {sim:.4f} (距離: {raw_dist:.4f}) | キー: 「{doc}」 -> 親: {parent_preview}")

                if raw_dist > EPISODE_SEARCH_MAX_DISTANCE:
                    continue

                if not is_worldline_ok:
                    continue

                if not parent_text or parent_text in seen_parents:
                    continue

                # Ranking（順位付け）用の風化ペナルティ (created_at基準)
                created_at_str = meta.get('created_at') or meta.get('timestamp', '') if meta else ''
                penalty = 0.0
                if created_at_str:
                    try:
                        mem_time = datetime.fromisoformat(created_at_str)
                        days = (now - mem_time).total_seconds() / 86400.0
                        penalty = min(EPISODE_DECAY_MAX_PENALTY, max(0.0, days * EPISODE_DECAY_RATE_PER_DAY))
                    except Exception:
                        pass
                ranking_dist = raw_dist + penalty
                ep_candidates.append((parent_text, raw_dist, ranking_dist, doc_id, meta))
                seen_parents.add(parent_text)

            # ランキング順にソート
            ep_candidates.sort(key=lambda x: x[2])

            # 上位 最大3件選定 & 合計 1,200 文字厳格キャップ
            selected_episodes = []
            episode_items = []
            total_len = 0
            accessed_ids = []

            for parent_text, raw_dist, ranking_dist, doc_id, meta in ep_candidates:
                sim = max(0.0, 1.0 - ranking_dist)
                if len(selected_episodes) == 0:
                    clipped = safe_sentence_truncate(parent_text, 650)
                    selected_episodes.append(clipped)
                    episode_items.append({
                        'type': 'episode',
                        'sim': sim,
                        'dist': ranking_dist,
                        'text': clipped,
                        'raw': clipped
                    })
                    total_len += len(clipped)
                    accessed_ids.append(doc_id)
                elif len(selected_episodes) >= 1 and raw_dist <= 0.130 and total_len < 1100:
                    remain_len = 1200 - total_len
                    clipped = safe_sentence_truncate(parent_text, remain_len)
                    if len(clipped) >= 40:
                        selected_episodes.append(clipped)
                        episode_items.append({
                            'type': 'episode',
                            'sim': sim,
                            'dist': ranking_dist,
                            'text': clipped,
                            'raw': clipped
                        })
                        total_len += len(clipped)
                        accessed_ids.append(doc_id)
                    if total_len >= 1200 or len(selected_episodes) >= 3:
                        break

            episode_memories = selected_episodes
            print(f"[INFO] 最終想起エピソード件数: {len(episode_memories)}件 / Fact件数: {len(fact_memories)}件")
            print(f"{'='*70}\n")

            # LRU: 想起されたエピソードの last_accessed を更新
            if accessed_ids:
                try:
                    now_iso = now.isoformat()
                    for doc_id in accessed_ids:
                        matching_meta = next((m for d_id, m in zip(e_ids, e_metas) if d_id == doc_id), None)
                        if matching_meta:
                            meta_copy = dict(matching_meta)
                            meta_copy['last_accessed'] = now_iso
                            collection.update(ids=[doc_id], metadatas=[meta_copy])
                except Exception as lru_err:
                    print(f"[INFO] LRU last_accessed update notice: {lru_err}")

        except Exception as ee:
            print(f"[WARN] Episode記憶検索警告: {ee}")

        if with_scores:
            return fact_items, episode_items
        return fact_memories, episode_memories
    except Exception as e:
        print(f"[WARN] プロキシ用記憶検索エラー: {e}")
        return ([], [])

def get_weather_forecast(location_name="東京"):
    """Open-Meteo APIを使用したリアルタイム天気予報取得"""
    try:
        # 地名から緯度経度を取得 (Nominatim OpenStreetMap)
        geo_url = f"https://nominatim.openstreetmap.org/search?q={location_name}&format=json&countrycodes=jp&limit=1"
        headers = {'User-Agent': 'DeiningSystem/1.0'}
        geo_resp = _http_session.get(geo_url, headers=headers, timeout=5)
        if geo_resp.status_code == 200 and geo_resp.json():
            geo_data = geo_resp.json()[0]
            lat = geo_data['lat']
            lon = geo_data['lon']
            display_name = geo_data.get('display_name', location_name)
        else:
            # フォールバック: 東京
            lat, lon, display_name = 35.6895, 139.6917, "東京都"

        # Open-Meteo API で今日・明日・明後日の天気を取得
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=weathercode,temperature_2m_max,temperature_2m_min,precipitation_probability_max&timezone=Asia%2FTokyo"
        w_resp = _http_session.get(weather_url, timeout=5)
        if w_resp.status_code == 200:
            w_data = w_resp.json()
            daily = w_data.get('daily', {})
            dates = daily.get('time', [])
            w_codes = daily.get('weathercode', [])
            max_temps = daily.get('temperature_2m_max', [])
            min_temps = daily.get('temperature_2m_min', [])
            precips = daily.get('precipitation_probability_max', [])

            # WMO Weather Code 解釈
            def code_to_text(code):
                if code == 0: return "快晴"
                if code in [1, 2, 3]: return "晴れ時々曇り"
                if code in [45, 48]: return "霧"
                if code in [51, 53, 55, 61, 63, 65, 80, 81, 82]: return "雨"
                if code in [71, 73, 75, 85, 86]: return "雪"
                if code in [95, 96, 99]: return "雷雨"
                return "曇り"

            lines = [f"【{location_name}（{display_name.split(',')[0]}）のリアルタイム気象予報】"]
            for i in range(min(len(dates), 3)):
                d_label = "今日" if i == 0 else ("明日" if i == 1 else "明後日")
                w_str = code_to_text(w_codes[i])
                lines.append(f"- {d_label} ({dates[i]}): {w_str} / 最高気温 {max_temps[i]}℃ / 最低気温 {min_temps[i]}℃ / 降水確率 {precips[i]}%")
            return "\n".join(lines)
    except Exception as e:
        print(f"[WARN] 天気予報API取得エラー: {e}")
    return None

def _extract_search_keywords(user_text):
    """自然な会話文からDuckDuckGoに最適な検索キーワードを抽出する"""
    text = user_text
    # ト書き (*...*) の除去
    text = re.sub(r'\*.*?\*', '', text)
    # かぎ括弧等の記号除去
    text = re.sub(r'[「」『』【】（）()！？!?、。]', ' ', text)
    
    # 依頼・会話の末尾フレーズ・口語表現・検索精度を落とす修飾語を徹底除去
    stop_phrases = [
        'って聞いたんだけど', 'と聞いたんだけど', 'って本当', 'という噂', 'って聞いた',
        '調べてくれない', '調べてほしい', '調べてみて', '調べて', '検索して', '教えて',
        'お願い', '頼む', 'ググって', 'ググる', 'ググり', 'どうなってる', 'どうなった', 'について',
        '知りたい', '最新の', '現在の', 'いまの', '今の', '誰か', '何か',
        '特徴', '概要', '詳細', 'とは'
    ]
    for sp in stop_phrases:
        text = text.replace(sp, ' ')

    raw_tokens = [t.strip() for t in text.split() if len(t.strip()) > 1]
    if not raw_tokens:
        return user_text.strip()

    # 検索エンジン（DuckDuckGo）でノイズとなり日付・時刻サイトを誤爆する時制語・指示語を除外
    noise_tokens = {
        '誰', '何', 'いつ', 'どこ', 'どう', 'これ', 'それ', 'あれ',
        '現在', '今', 'いま', '最新', 'リアルタイム', '今日', '今年', '最近'
    }
    meaningful_tokens = [t for t in raw_tokens if t not in noise_tokens]
    
    # 全滅した場合は元のトークンを使用
    chosen_tokens = meaningful_tokens if meaningful_tokens else raw_tokens

    # 重複する4桁年号（例: 2024 と 2025）の過剰併記を整理（最新1つに統一）
    cleaned_tokens = []
    seen_years = []
    for t in chosen_tokens:
        if re.fullmatch(r'20\d\d', t):
            seen_years.append(t)
        else:
            cleaned_tokens.append(t)
    if seen_years:
        cleaned_tokens.append(sorted(seen_years)[-1])

    # DuckDuckGo日本語検索は3〜4単語が最も適合率が高いため上限を設ける
    if len(cleaned_tokens) > 4:
        cleaned_tokens = cleaned_tokens[:4]

    if cleaned_tokens:
        return ' '.join(cleaned_tokens)
    return user_text.strip()

def _do_web_search(query_text, session_id='default_session'):
    """プロキシ用: 天気API + DuckDuckGoニュース/スマートWeb検索 (インメモリキャッシュ付き, チャットセッション対応)"""
    cache_key = query_text.strip().lower()
    now_ts = time.time()
    active_sid = session_id or 'default_session'

    _prune_search_cache(now_ts)

    # インメモリキャッシュの確認 (1時間有効 / Flask終了時に自動破棄)
    with _SEARCH_CACHE_LOCK:
        cached = _SEARCH_CACHE.get(cache_key)
    if cached and now_ts - cached.get('timestamp', 0) < _CACHE_TTL_SECONDS:
        _search_logger.debug(f"[キャッシュヒット] クエリ='{query_text}' (session={active_sid})")
        cached_results = cached.get('results', [])
        if cached_results:
            # 同一クエリでも現在のチャットセッションの即席リトリーバルに登録し、このチャット内での想起を可能にする
            _register_ephemeral_web_retrieval(query_text, cached_results, session_id=active_sid)
        return cached_results

    # 1. 天気関連クエリの判定
    if any(k in query_text for k in ['天気', '気温', '雨', '晴れ', '台風', '気象', '降水確率']):
        loc = "東京"
        # クエリから地名を抽出
        words = query_text.replace('調べて', '').replace('検索して', '').replace('教えて', '').replace('天気', '').replace('明日', '').replace('今日', '').replace('明後日', '').replace('の', ' ').split()
        for word in words:
            if word.strip() and not word.startswith('*'):
                loc = word.strip()
                break
        weather_res = get_weather_forecast(loc)
        if weather_res:
            _search_logger.debug(f"[気象API] 地名='{loc}' (session={active_sid})\n{weather_res}")
            results_formatted = [weather_res]
            with _SEARCH_CACHE_LOCK:
                _SEARCH_CACHE[cache_key] = {"results": results_formatted, "timestamp": now_ts}
            _register_ephemeral_web_retrieval(query_text, results_formatted, session_id=active_sid)
            return results_formatted

    # 2. 一般Web検索・ニュース検索 (DuckDuckGo)
    try:
        clean_query = _extract_search_keywords(query_text)
        
        results = []
        start_time = time.time()
        TOTAL_TIMEOUT = 10.0
        BACKEND_TIMEOUT = 3

        # 時事問題・ニュース系のクエリならDDGS news検索を優先
        is_news_likely = any(k in query_text for k in ['総理', '首相', '大臣', '大統領', '政治', '事件', '事故', '速報', 'ニュース', '発表', '発売', '引退', '結婚', '最新'])
        if is_news_likely:
            try:
                time_left = TOTAL_TIMEOUT - (time.time() - start_time)
                if time_left > 0:
                    news_timeout = max(1, min(BACKEND_TIMEOUT, int(time_left)))
                    news_items = list(DDGS(timeout=news_timeout).news(clean_query, max_results=5, region='jp-jp'))
                    for r in news_items:
                        results.append({
                            'title': r.get('title', ''),
                            'body': f"{r.get('date', '')[:10]} - {r.get('body', '')}",
                            'href': r.get('url', '') or r.get('href', '')
                        })
            except Exception as ne:
                print(f"[WARN] DuckDuckGo News検索エラー (通常検索へフォールバック): {ne}")

        # 通常テキスト検索 (多段階フォールバック: auto -> HTML -> Lite -> 短縮キーワード -> Wikipedia)
        def _filter_valid_results(items, query):
            """DuckDuckGoがヒット0件時に返してくる無関係な海外英語記事や、
            クエリの主要キーワードを一切含まない無関係な埋め合わせ結果を排除"""
            has_jp = bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', query))
            
            # クエリから一般的な汎用語（特徴・アプリ・情報等）を除いた重要照合キーワードを抽出
            q_tokens = [w.lower() for w in re.split(r'[\s　,、。！？!?]+', query) if len(w) > 1]
            generic_stop = {'特徴', '内容', '概要', '詳細', '情報', 'まとめ', 'とは', 'アプリ', 'サイト', '最新', '現在'}
            key_tokens = [t for t in q_tokens if t not in generic_stop]
            if not key_tokens:
                key_tokens = q_tokens

            valid = []
            for it in items:
                title = it.get('title', '')
                body = it.get('body', '')
                content_lower = f"{title} {body}".lower()

                if has_jp:
                    # 日本語クエリの場合、title または body に日本語が含まれていること
                    if not re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', content_lower):
                        _search_logger.debug(f"[検索除外(非日本語スパム)] {title}")
                        continue

                # クエリの主要キーワード（key_tokens）のいずれかがタイトルまたは本文に含まれていること
                # （DuckDuckGoが0件時に返してくる全く無関係なYouTube動画や相談窓口等を排除）
                if key_tokens and not any(kw in content_lower for kw in key_tokens):
                    _search_logger.debug(f"[検索除外(キーワード不一致)] 期待={key_tokens} | 除外={title}")
                    continue

                valid.append(it)
            return valid

        if not results:
            for backend in ('auto', 'html', 'lite'):
                time_left = TOTAL_TIMEOUT - (time.time() - start_time)
                if time_left <= 0:
                    _search_logger.debug("DDGS タイムアウト予算超過 (10s)")
                    break
                try:
                    call_timeout = max(1, min(BACKEND_TIMEOUT, int(time_left)))
                    res = list(DDGS(timeout=call_timeout).text(clean_query, max_results=5, region='jp-jp', backend=backend))
                    res = _filter_valid_results(res, clean_query)
                    if res:
                        results = res
                        break
                except Exception as be:
                    _search_logger.debug(f"DDGS backend={backend} 失敗: {be}")
                    continue

            # 依然として0件なら短縮キーワードでリトライ
            if not results:
                words = clean_query.split()
                if len(words) > 2:
                    short_kw = ' '.join(words[:2])
                    print(f"[INFO] 短縮キーワードで再試行: '{short_kw}'")
                    for backend in ('auto', 'html', 'lite'):
                        time_left = TOTAL_TIMEOUT - (time.time() - start_time)
                        if time_left <= 0:
                            break
                        try:
                            call_timeout = max(1, min(BACKEND_TIMEOUT, int(time_left)))
                            res = list(DDGS(timeout=call_timeout).text(short_kw, max_results=5, region='jp-jp', backend=backend))
                            res = _filter_valid_results(res, short_kw)
                            if res:
                                results = res
                                break
                        except Exception:
                            continue

        # Wikipedia 日本語概要検索による知識補完 (既定で有効、DEINEI_WIKI_FALLBACK=0 で無効化)
        wiki_fallback_enabled = os.environ.get('DEINEI_WIKI_FALLBACK', '1').strip() != '0'
        has_wiki = any('wikipedia' in (r.get('href', '') or '').lower() for r in results)
        if wiki_fallback_enabled and (not results or not has_wiki):
            time_left = TOTAL_TIMEOUT - (time.time() - start_time)
            if time_left > 0:
                try:
                    words = clean_query.split()
                    wiki_candidates = []
                    if words:
                        wiki_candidates.append(words[0])  # 人物・作家名・主語（例: 斜線堂有紀）
                    if len(words) >= 2:
                        wiki_candidates.append(' '.join(words[:2]))
                    if clean_query not in wiki_candidates:
                        wiki_candidates.append(clean_query)

                    for wiki_target in wiki_candidates:
                        call_timeout = min(float(BACKEND_TIMEOUT), max(1.0, float(time_left)))
                        headers = {'User-Agent': 'Deinei-AI-Assistant/1.0 (https://github.com/kakushiarerukea/Deinei)'}
                        
                        # Step 1: Wikipedia REST Search API で記事キーを検索（大文字小文字・表記揺れ対応）
                        search_url = f"https://ja.wikipedia.org/w/rest.php/v1/search/page?q={urllib.parse.quote(wiki_target)}&limit=1"
                        target_key = None
                        target_title = None
                        try:
                            s_res = _http_session.get(search_url, timeout=call_timeout, headers=headers)
                            if s_res.status_code == 200:
                                s_data = s_res.json()
                                pages = s_data.get('pages', [])
                                if pages:
                                    target_key = pages[0].get('key')
                                    target_title = pages[0].get('title', wiki_target)
                        except Exception as se:
                            _search_logger.debug(f"Wikipedia 検索APIエラー: {se}")

                        # 検索で見つからない場合は従来の直接指定をフォールバックとして試行
                        lookup_key = target_key or wiki_target

                        # Step 2: 記事サマリーの取得
                        wiki_url = f"https://ja.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(lookup_key)}"
                        w_res = _http_session.get(wiki_url, timeout=call_timeout, headers=headers)
                        if w_res.status_code == 200:
                            w_data = w_res.json()
                            extract = w_data.get('extract', '')
                            w_title = w_data.get('title', target_title or wiki_target)
                            w_page = w_data.get('content_urls', {}).get('desktop', {}).get('page', '')
                            if extract and len(extract) > 30:
                                wiki_item = {
                                    'title': f"{w_title} (Wikipedia)",
                                    'body': extract,
                                    'href': w_page
                                }
                                # Wikipediaの解説を先頭に挿入
                                results.insert(0, wiki_item)
                                print(f"[INFO] Wikipedia から概要を取得・補完しました: {w_title}")
                                break
                        else:
                            _search_logger.debug(f"[Wikipedia] 該当なし/404: key='{lookup_key}' (HTTP {w_res.status_code})")
                except Exception as we:
                    _search_logger.debug(f"Wikipedia 取得エラー: {we}")

        # Weblio等の「記事の著作権〜」ライセンス注意書きのみで本文がない無益スニペットを排除
        results = [
            r for r in results 
            if not ('All text is available under the terms of the GNU Free Documentation License' in r.get('body', '') and len(r.get('body', '')) < 200)
        ]

        # 最大5件に制限
        results = results[:5]

        formatted = []
        for r in results:
            title = r.get('title', '')
            body = r.get('body', '')
            href = r.get('href', '')
            if title and body:
                formatted.append(f"【{title}】\n{body}\n(出典: {href})")

        # 詳細はログファイルに、コンソールにはサマリーのみ
        _search_logger.debug(f"[Web検索] キーワード='{clean_query}' 取得件数={len(formatted)}\n" + '\n---\n'.join(formatted))

        # キャッシュに保存 & 即席リトリーバルに登録
        if formatted:
            with _SEARCH_CACHE_LOCK:
                _SEARCH_CACHE[cache_key] = {"results": formatted, "timestamp": now_ts}
            _register_ephemeral_web_retrieval(query_text, formatted, session_id=active_sid)

        return formatted
    except Exception as e:
        print(f"[WARN] プロキシ用Web検索エラー: {e}")
        return []

def _extract_and_save_fact_bg(user_text, session_id='default_session'):
    """バックグラウンドで事実抽出・保存（プロキシ用）"""
    if not user_text or not user_text.strip():
        return
    try:
        collection = get_memory_collection(session_id)
        # llama-serverに事実抽出を依頼 (英語プロンプト・会話言語適応)
        fact_prompt = f"""Extract concrete, personal facts about the user (preferences, experiences, promises, relationships, events) from the conversation log below.
- Output one fact per line, starting with "- ".
- Exclude overly generic interactions; extract only personal facts worth long-term memory.
- If no new facts are found, output ONLY "None" (or "なし").
- IMPORTANT: Output in the primary language used in the conversation log.

{user_text}"""
        payload = {
            "model": "",  # llama-serverのデフォルトモデルを使用
            "messages": [
                {"role": "system", "content": "You are a factual memory extraction engine. Extract only personal facts about the user from the conversation log as concise bullet points, matching the conversation's primary language."},
                {"role": "user", "content": fact_prompt}
            ],
            "stream": False,
            "temperature": 0.2,
            "max_tokens": 1024,
            "chat_template_kwargs": {"enable_thinking": False}
        }
        resp = _http_session.post(f"{LLAMA_SERVER_URL}/v1/chat/completions", json=payload, timeout=60)
        if resp.status_code == 200:
            result = resp.json()
            extracted = result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
            if extracted and extracted != 'なし' and '事実が見つからない' not in extracted:
                from datetime import datetime
                lines = [normalize_fact_text(line.strip().lstrip('-').strip()) for line in extracted.split('\n') if line.strip().startswith('-')]
                lines = [l for l in lines if l]
                if lines:
                    ids = [str(uuid.uuid4()) for _ in lines]
                    metadatas = [{"timestamp": datetime.now().isoformat(), "type": "fact", "channel_id": "web_ui"} for _ in lines]
                    collection.add(documents=lines, ids=ids, metadatas=metadatas)
                    print(f"[INFO] 新しい記憶を {len(lines)} 件保存しました")
    except Exception as e:
        print(f"[WARN] バックグラウンド事実抽出エラー: {e}")

def _decide_and_generate_search_query(user_text, recent_history=None, user_content=None):
    """Stage 1: LLM (Gemma 4 / 26B / mmproj) に直近会話文脈および添付画像を踏まえた検索要否判断と最適キーワード生成を行わせる
    - Reasoning無効化 (enable_thinking: False) による超低遅延パス (~0.1秒)
    - max_tokens=512
    - 直前会話履歴および画像内容を参照して「この本」「その人」等の指示語を自動解決
    """
    if not user_text and not user_content:
        return None

    try:
        system_inst = (
            "あなたは会話の文脈や画像情報からWeb検索の要否を判断し、最適な検索キーワードを生成するエキスパートです。\n"
            "ユーザーの発言や直前の会話の流れを踏まえ、最新ニュース、時事情報、事実確認、天気、知識、あるいは画像内の対象（書籍・作品・商品・人物・建築物・用語など）に関するWeb検索が必要な場合は「SEARCH: <検索エンジンに入力すべき最適な単語群>」と1行で出力してください。\n"
            "【重要ルール】\n"
            "1. 画像が添付されている場合（本の表紙、商品パッケージ、スクリーンショットなど）は、画像内の文字（タイトル、著者名、商品名、ロゴなど）や被写体を視覚的に正確に読み取り、具体的な固有名詞を検索単語に必ず含めてください（例：書籍表紙画像と「この本の内容を検索して」-> 画像から書名と著者を読み取り「SEARCH: 斜線堂有紀 星が人を愛すことなかれ」など）。\n"
            "2. 発言内の「その人」「彼」「あれ」「それ」「この本」等の指示語は、直前の会話文脈や画像情報から具体的な固有名詞に解決して検索単語に含めてください。\n"
            "3. 検索キーワードは検索エンジン（DuckDuckGo）で確実にヒットするよう、2〜3単語程度の具体的・端的な名詞に絞ってください。「あらすじ」「内容」「詳細」「まとめ」「概要」「情報」「特徴」などの修飾語や、「現在」「今」「最新」「リアルタイム」「最近」などの抽象的な時制ワードは検索ノイズとなりヒット件数を激減させるため絶対に含めないでください（例: 書籍について調べる場合 -> 「SEARCH: 斜線堂有紀 星が人を愛すことなかれ」など名詞のみ）。\n"
            "4. 相手（キャラクター）自身への個人的な質問や希望・感情・スキンシップ（例：「明日行きたい場所ある？」「どこか行こうか」「何が好き？」「手を繋ごう」「ぎゅっとして」など）は、Web情報検索ではなくキャラクターの内心・対話への問いかけであるため、Web検索は不要です（必ず「NONE」とだけ出力してください）。\n"
            "5. すでに直前の会話で回答済みの内容、挨拶、日常会話、感情表現、キャラクターへのスキンシップ、沈黙、単なる雑談などWeb検索が不要な場合は「NONE」とだけ出力してください。\n"
            "前置きや思考（thought）、解説、セリフ、ト書きは一切出力せず、指定の出力のみを行ってください。"
        )

        stage1_messages = [{"role": "system", "content": system_inst}]
        if recent_history:
            # 直近の会話履歴（最大4件）を追加
            for m in recent_history[-4:]:
                role = m.get('role')
                c = m.get('content', '')
                if role in ['user', 'assistant'] and c:
                    if isinstance(c, list):
                        c = ' '.join(item.get('text', '') for item in c if isinstance(item, dict) and item.get('type') == 'text')
                    # 長すぎるアシスタント返答は先頭200文字に要約してStage 1へ渡す
                    if role == 'assistant' and len(c) > 200:
                        c = c[:200] + '...'
                    stage1_messages.append({"role": role, "content": c})

        has_img = False
        if isinstance(user_content, list):
            has_img = any(isinstance(it, dict) and it.get('type') == 'image_url' for it in user_content)

        # 画像データが含まれている場合は user_content（list形式）をそのまま渡してマルチモーダル認識を有効化
        if user_content is not None:
            stage1_messages.append({"role": "user", "content": user_content})
        else:
            stage1_messages.append({"role": "user", "content": user_text})

        payload = {
            "model": "",
            "messages": stage1_messages,
            "stream": False,
            "temperature": 0.1,
            "max_tokens": 512,
            "chat_template_kwargs": {"enable_thinking": False}
        }

        # 画像処理（mmproj）のエンコード時間を考慮し、画像添付時は45秒まで待機
        timeout_sec = 45 if has_img else 15
        resp = _http_session.post(f"{LLAMA_SERVER_URL}/v1/chat/completions", json=payload, timeout=timeout_sec)
        if resp.status_code == 200:
            result = resp.json()
            msg_obj = result.get('choices', [{}])[0].get('message', {})
            raw_output = msg_obj.get('content', '').strip()
            reasoning = msg_obj.get('reasoning_content', '')
            _search_logger.debug(f"[Stage 1] content={repr(raw_output)} | reasoning={repr(reasoning)}")

            # content が空の場合は検索なしとして扱う
            if not raw_output:
                return None

            # <thought> / <think> タグが含まれている場合は除去
            clean_output = re.sub(r'<thought>.*?</thought>', '', raw_output, flags=re.DOTALL).strip()
            clean_output = re.sub(r'<think>.*?</think>', '', clean_output, flags=re.DOTALL).strip()
            # 改行がある場合は先頭行を採用
            first_line = clean_output.split('\n')[0].strip()

            if first_line.startswith('SEARCH:'):
                search_query = first_line.replace('SEARCH:', '').strip()
                if search_query and search_query.upper() != 'NONE':
                    return search_query
                return STAGE1_NONE
            elif first_line.upper() == 'NONE' or 'NONE' in first_line.upper() or any(neg in first_line for neg in ['不要', 'なし']):
                return STAGE1_NONE
            else:
                # SEARCH: プレフィックスがない場合の安全検証
                # セリフ・ト書き（カッコ・アスタリスクなど）が含まれている場合はロールプレイ暴走とみなして破棄
                if any(p in first_line for p in ['（', '）', '(', ')', '*', '「', '」', '……', '...']):
                    _search_logger.debug(f"[Stage 1 RP暴走検知] ロールプレイ出力を破棄: {repr(first_line)}")
                    return STAGE1_NONE
                # 長文（25文字以上）や会話調（です/ます/した/私/あなた/君など）が含まれている場合も破棄
                if len(first_line) > 25 or any(w in first_line for w in ['です', 'ます', 'した', '私', 'あなた', '君', '僕', 'はるか']):
                    _search_logger.debug(f"[Stage 1 会話調検知] 長文・セリフ出力を破棄: {repr(first_line)}")
                    return STAGE1_NONE
                # 短い単語列（名詞2〜3語など）の場合のみフォールバック許可
                if len(first_line) > 1:
                    return first_line
                return STAGE1_NONE
        else:
            print(f"[WARN] Stage 1 リクエスト失敗 (ステータス: {resp.status_code})")
            return None
    except Exception as e:
        print(f"[WARN] Stage 1 検索判定エラー: {e}")
        return None

# ====== Web検索オン/オフ トグル API ======
@app.route('/api/search/toggle', methods=['POST'])
def toggle_search():
    """Web検索機能のオン/オフを切り替え（enabled 引数があれば直接設定）"""
    global _SEARCH_ENABLED
    data = _get_json_dict(silent=True)
    if 'enabled' in data:
        _SEARCH_ENABLED = bool(data['enabled'])
    else:
        _SEARCH_ENABLED = not _SEARCH_ENABLED
    status = "ON" if _SEARCH_ENABLED else "OFF"
    print(f"[INFO] Web検索: {status}")
    return jsonify({'search_enabled': _SEARCH_ENABLED, 'status': status})

@app.route('/api/search/status', methods=['GET'])
def search_status():
    """Web検索機能の現在の状態を取得"""
    _prune_search_cache()
    with _SEARCH_CACHE_LOCK:
        cache_count = len(_SEARCH_CACHE)
    return jsonify({
        'search_enabled': _SEARCH_ENABLED,
        'trigger_keywords': _SEARCH_TRIGGER_KEYWORDS,
        'cache_entries': cache_count
    })

_LAST_REQUEST_MESSAGES_BY_SESSION = {}
_LAST_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_MAX_SESSIONS = 32

def _debug_log_token_cache_diff(current_messages, session_id='default_session'):
    """トークン共通プレフィックス比較デバッグ。
    前回リクエストと今回リクエストのトークン列をセッション単位で比較し、KVキャッシュ再利用率と食い違い位置をログに出力する。
    """
    if not current_messages:
        return

    clean_current = []
    for m in current_messages:
        if isinstance(m, dict) and 'role' in m:
            clean_current.append({
                'role': m['role'],
                'content': m.get('content', '')
            })

    sid_key = str(session_id or 'default_session')
    with _LAST_REQUEST_LOCK:
        prev_messages = _LAST_REQUEST_MESSAGES_BY_SESSION.get(sid_key)
        _LAST_REQUEST_MESSAGES_BY_SESSION[sid_key] = clean_current
        if len(_LAST_REQUEST_MESSAGES_BY_SESSION) > _LAST_REQUEST_MAX_SESSIONS:
            oldest_sid = next(iter(_LAST_REQUEST_MESSAGES_BY_SESSION))
            if oldest_sid != sid_key:
                _LAST_REQUEST_MESSAGES_BY_SESSION.pop(oldest_sid, None)

    if not prev_messages:
        try:
            r = _http_session.post(f"{LLAMA_SERVER_URL}/apply-template", json={"messages": clean_current}, timeout=3)
            prompt = r.json().get("prompt", "")
            r2 = _http_session.post(f"{LLAMA_SERVER_URL}/tokenize", json={"content": prompt, "parse_special": True}, timeout=3)
            tokens = r2.json().get("tokens", [])
            print(f"[INFO] [Token Cache Debug] 初回ターン 総トークン数: {len(tokens)} (session={sid_key})")
        except Exception:
            pass
        return

    try:
        r_prev = _http_session.post(f"{LLAMA_SERVER_URL}/apply-template", json={"messages": prev_messages}, timeout=3)
        prompt_prev = r_prev.json().get("prompt", "")
        tok_prev = _http_session.post(f"{LLAMA_SERVER_URL}/tokenize", json={"content": prompt_prev, "parse_special": True}, timeout=3).json().get("tokens", [])

        r_curr = _http_session.post(f"{LLAMA_SERVER_URL}/apply-template", json={"messages": clean_current}, timeout=3)
        prompt_curr = r_curr.json().get("prompt", "")
        tok_curr = _http_session.post(f"{LLAMA_SERVER_URL}/tokenize", json={"content": prompt_curr, "parse_special": True}, timeout=3).json().get("tokens", [])

        n = 0
        for x, y in zip(tok_prev, tok_curr):
            if x != y:
                break
            n += 1

        reuse_pct = (n / len(tok_prev) * 100) if tok_prev else 0
        print(f"\n==================== [Token Cache Debug] ====================")
        print(f"前回: {len(tok_prev)} tok | 今回: {len(tok_curr)} tok | 共通プレフィックス（キャッシュ再利用）: {n} tok ({reuse_pct:.1f}%)")

        if n < len(tok_prev) and n < len(tok_curr):
            d_prev = _http_session.post(f"{LLAMA_SERVER_URL}/detokenize", json={"tokens": tok_prev[max(0, n-15): n+15]}, timeout=3).json().get("content", "")
            d_curr = _http_session.post(f"{LLAMA_SERVER_URL}/detokenize", json={"tokens": tok_curr[max(0, n-15): n+15]}, timeout=3).json().get("content", "")
            print(f"[WARN] 食い違い位置: {n} トークン目")
            print(f"  前回(n±15): {repr(d_prev)}")
            print(f"  今回(n±15): {repr(d_curr)}")
        else:
            print(f"[OK] 前回の全トークン ({len(tok_prev)} tok) がキャッシュ再利用されました！")
        print(f"=============================================================\n")
    except Exception as e:
        print(f"[Token Cache Debug] 比較エラー（無視可能）: {e}")

def _enrich_messages_with_rag(payload, messages, user_text, raw_user_content, session_id, channel_id, active_message_ids, _is_internal_request):
    """RAG記憶検索・Web検索・コンテキスト情報を安全に注入する"""
    fact_memories = []
    episode_memories = []
    web_recalled_results = []
    context_text = ""

    # RAG: 関連記憶の検索と注入（通常チャットのみ。要約・事実抽出リクエストはスキップ）
    if (user_text or raw_user_content) and not _is_internal_request:
        fact_items, episode_items = _search_memory_for_proxy(
            user_text,
            session_id=session_id,
            active_message_ids=active_message_ids,
            channel_id=channel_id,
            n_results=6,
            with_scores=True
        )
        web_context = ''
        web_recalled_results = []
        fact_memories = []
        episode_memories = []
        memory_context = ''

        # 沈黙・空プロンプト（"……", "...", "…", "（……）", "（沈黙）" など）の判定
        clean_user_text = user_text.strip()
        is_silence_prompt = clean_user_text in ['……', '…', '...', '..', '.', '（……）', '(……)', '（…）', '(…)', '（沈黙）', '(沈黙)'] or len(clean_user_text) == 0

        silence_instruction = ""
        if is_silence_prompt:
            silence_instruction = (
                "【ユーザーの沈黙・場面展開指示】\n"
                "ユーザーは言葉を発さず沈黙し、相手（あなた）の自発的な反応や次の展開を待っています。\n"
                "単に「どうしましたか？」と聞き返したり無言を繰り返すのではなく、沈黙を受け止めつつ能動的にストーリーを進展させてください。\n"
                "（例：現在の情景や自身の動作・心理をト書き（*...*）で描写する、沈黙の意図を汲んで新たな話題や提案を切り出す、次の行動を起こすなど）\n\n"
            )
        else:
            # ハイブリッド検索: キーワードトリガー（KVキャッシュ保全）+ LLMクエリ最適化
            # 雑談・RP等はここをスキップ → LLM呼び出しゼロ → KVキャッシュ完全保全
            has_image = False
            if isinstance(raw_user_content, list):
                has_image = any(isinstance(it, dict) and it.get('type') == 'image_url' for it in raw_user_content)

            req_search_enabled = payload.get('search_enabled', _SEARCH_ENABLED)
            should_check_search = req_search_enabled and should_trigger_search(clean_user_text)

            if should_check_search:
                # 明示的検索意図を検知 → LLM (Stage 1) で文脈や画像を踏まえた最適検索ワードを生成
                try:
                    recent_history = [m for m in messages[:-1] if m.get('role') in ['user', 'assistant']]
                    stage1_res = _decide_and_generate_search_query(user_text, recent_history, user_content=raw_user_content)
                except Exception as e:
                    _search_logger.debug(f"[Stage 1 例外] {type(e).__name__}: {e}")
                    stage1_res = None

                # Stage 1 の戻り値処理:
                # - STAGE1_NONE: LLMが「検索不要」と明示判定 -> 検索せず、フォールバックも行わない
                # - 有効文字列: そのクエリで検索実行
                # - None: Stage 1の例外・タイムアウト・HTTPエラー・空応答時のみキーワード抽出にフォールバック
                if stage1_res is STAGE1_NONE:
                    search_query = None
                elif stage1_res:
                    search_query = stage1_res
                else:
                    search_query = _extract_search_keywords(clean_user_text)

                if search_query:
                    print(f"[INFO] 検索: '{search_query}'")
                    web_results = _do_web_search(search_query, session_id=session_id)
                    if web_results:
                        web_recalled_results = web_results
                        web_context = '【リアルタイムWeb検索・気象データ】\n' + '\n---\n'.join(web_results) + '\n\n'

            # =========================================================================
            # 統合RAGランキング (Unified Top-6 Selection)
            # Fact記憶・Episode記憶（親子）・即席Web記憶の全候補を類似度比較し、上位6件を厳格採用
            # =========================================================================
            rag_candidates = []
            for it in fact_items:
                rag_candidates.append(it)
            for it in episode_items:
                rag_candidates.append(it)

            # 新規リアルタイム検索が発火しなかった場合、即席Web記憶もプールに含める
            if req_search_enabled and not web_recalled_results:
                ephemeral_items = _search_ephemeral_web_for_proxy(clean_user_text, session_id=session_id, n_results=6, with_scores=True)
                for it in ephemeral_items:
                    rag_candidates.append(it)

            # 類似度 (sim) 降順でソートし、全体で上位6件を厳格採用
            rag_candidates.sort(key=lambda x: x['sim'], reverse=True)
            top6_selected = rag_candidates[:6]

            if top6_selected:
                print(f"\n{'='*25} 統合RAG想起ランキング (Top 6 厳選) {'='*25}")
                print(f"総候補数: {len(rag_candidates)}件 -> 採用: {len(top6_selected)}件")
                for rank, item in enumerate(top6_selected, 1):
                    preview = item['text'].replace('\n', ' ')[:60]
                    print(f"  #{rank} [{item['type'].upper():7s}] 類似度: {item['sim']:.4f} | {preview}...")
                print(f"{'='*75}\n")

            # 採用されたアイテムをカテゴリ別に抽出
            sel_facts = [c for c in top6_selected if c['type'] == 'fact']
            sel_episodes = [c for c in top6_selected if c['type'] == 'episode']
            sel_webs = [c for c in top6_selected if c['type'] == 'web']

            fact_memories = [c['raw'] for c in sel_facts]
            episode_memories = [c['raw'] for c in sel_episodes]
            if sel_webs:
                web_recalled_results = [c['raw'] for c in sel_webs]

            # セクション構築: 最も類似度が高いカテゴリを先頭に配置（動的セクション順）
            sections_with_scores = []
            if sel_facts:
                f_text = '【関連する過去の事実・記録】\n' + '\n'.join(c['text'] for c in sel_facts) + '\n\n'
                sections_with_scores.append((max(c['sim'] for c in sel_facts), f_text))
            if sel_episodes:
                e_formatted = [f"[当時のエピソード {idx+1}]:\n{c['text']}" for idx, c in enumerate(sel_episodes)]
                e_text = '【関連する過去のエピソード記憶（回想）】\n' + '\n\n'.join(e_formatted) + '\n\n'
                sections_with_scores.append((max(c['sim'] for c in sel_episodes), e_text))
            if sel_webs:
                w_text = '【直近の検索から想起されたWeb知識（過去5時間以内）】\n' + '\n---\n'.join(c['text'] for c in sel_webs) + '\n\n'
                sections_with_scores.append((max(c['sim'] for c in sel_webs), w_text))

            sections_with_scores.sort(key=lambda x: x[0], reverse=True)
            memory_context = ''.join(s[1] for s in sections_with_scores)

        from datetime import datetime
        now_str = datetime.now().strftime("%Y/%m/%d %H:%M:%S")

        # システムプロンプト（最先頭）に追記したり独立したsystemメッセージを挟むと、
        # 過去ログとの境界でKVキャッシュ（Prefix Caching）が破壊されるため、
        # 動的情報（現在時刻・沈黙指示・RAG記憶・Web検索）は最新のユーザーメッセージの末尾にアタッチしてキャッシュを保護する。
        # ※ 過去のターンはフロントエンド側で素のテキストが保持されるため、コンテキスト（150tok）が過去ログに蓄積されることは一切なく、
        #    かつ直前ターンの直前までの過去ログ（数千トークン）は100%キャッシュ再利用される。
        context_text = (
            f"### 現在のコンテキスト情報（回答の参考にしてください）\n"
            f"【現在日時】: {now_str}\n"
            f"{silence_instruction}"
            f"{memory_context}{web_context}"
        ).strip()
        
        # 最新のユーザーメッセージを探して末尾に付加する
        last_user_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get('role') == 'user':
                last_user_idx = i
                break

        if last_user_idx != -1:
            user_msg = messages[last_user_idx]
            u_content = user_msg.get('content', '')
            if isinstance(u_content, str):
                if u_content.strip():
                    user_msg['content'] = f"{u_content}\n\n[コンテキスト情報]\n{context_text}"
                else:
                    user_msg['content'] = f"[コンテキスト情報]\n{context_text}"
            elif isinstance(u_content, list):
                # マルチモーダル (テキスト + 画像)
                text_appended = False
                for item in reversed(u_content):
                    if isinstance(item, dict) and item.get('type') == 'text':
                        item['text'] = f"{item.get('text', '')}\n\n[コンテキスト情報]\n{context_text}"
                        text_appended = True
                        break
                if not text_appended:
                    u_content.append({'type': 'text', 'text': f"[コンテキスト情報]\n{context_text}"})
                user_msg['content'] = u_content
        else:
            messages.append({'role': 'user', 'content': f"[コンテキスト情報]\n{context_text}"})
            
        payload['messages'] = messages

    # デバッグ: トークン共通プレフィックス比較（Claude提案ロジック）
    if not _is_internal_request:
        _debug_log_token_cache_diff(messages, session_id=session_id)

    return fact_memories, episode_memories, web_recalled_results, context_text

@app.route('/v1/chat/completions', methods=['POST', 'OPTIONS'])
def proxy_chat_completions():
    """llama-server への /v1/chat/completions プロキシ。
    RAGメモリ検索とWeb検索の結果をシステムプロンプトに注入し、
    SSEストリーミングをそのまま中継する。
    """
    if request.method == 'OPTIONS':
        return '', 204

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not payload:
        return jsonify({'error': 'no payload'}), 400

    messages = payload.get('messages', [])
    if not isinstance(messages, list):
        messages = []
    user_text = ''
    raw_user_content = None

    # 最後のユーザーメッセージを取得
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get('role') == 'user':
            content = msg.get('content', '')
            raw_user_content = content
            if isinstance(content, str):
                user_text = content
            elif isinstance(content, list):
                user_text = ' '.join(
                    item.get('text', '') for item in content
                    if isinstance(item, dict) and item.get('type') == 'text'
                )
            break

    # 要約・事実抽出・子要素索引リクエストの判定（RAG/検索を適用してはならない）
    # フロントエンドの要約/事実抽出/子要素抽出は独自のシステムプロンプトで送信される
    _is_internal_request = False
    if messages:
        first_sys = next((m.get('content', '') for m in messages if m.get('role') == 'system'), '')
        if isinstance(first_sys, str) and any(marker in first_sys for marker in [
            '要約マシン', '事実抽出マシン', 'エピソード索引マシン',
            'objective summarization engine', 'factual memory extraction engine', 'episode indexing engine'
        ]):
            _is_internal_request = True

    session_id = payload.get('session_id') or request.args.get('session_id') or 'default_session'
    channel_id = payload.get('channel_id') or request.args.get('channel_id') or 'web_ui'
    active_message_ids = payload.get('active_message_ids') or [
        m.get('id') for m in messages if isinstance(m, dict) and m.get('id')
    ]

    # RAG: 関連記憶の検索と注入（例外時は通常チャット推論へ安全フォールバック）
    try:
        fact_memories, episode_memories, web_recalled_results, context_text = _enrich_messages_with_rag(
            payload, messages, user_text, raw_user_content, session_id, channel_id, active_message_ids, _is_internal_request
        )
    except Exception as e:
        print(f"[WARN] RAG想起・前処理エラー（通常チャット推論にフォールバックします）: {e}", flush=True)
        import traceback
        traceback.print_exc()
        fact_memories, episode_memories, web_recalled_results, context_text = [], [], [], ""

    is_stream = payload.get('stream', False)

    try:
        # llama-server にプロキシ
        headers = {'Content-Type': 'application/json'}
        # 元のリクエストの認証ヘッダーがあれば転送
        if request.headers.get('Authorization'):
            headers['Authorization'] = request.headers['Authorization']

        resp = _http_session.post(
            f"{LLAMA_SERVER_URL}/v1/chat/completions",
            json=payload,
            headers=headers,
            stream=is_stream,
            timeout=300
        )

        recalled_data = {
            "facts": fact_memories if 'fact_memories' in locals() else [],
            "episodes": episode_memories if 'episode_memories' in locals() else [],
            "web": web_recalled_results if 'web_recalled_results' in locals() else [],
            "injected_context": context_text if 'context_text' in locals() else ""
        }
        recalled_b64 = base64.b64encode(json.dumps(recalled_data, ensure_ascii=False).encode('utf-8')).decode('ascii')

        if is_stream:
            # SSEストリーミング中継
            def generate():
                try:
                    for chunk in resp.iter_content(chunk_size=None):
                        if chunk:
                            yield chunk
                except Exception as e:
                    print(f"[WARN] SSEストリーム中継エラー: {e}")
                finally:
                    resp.close()

            response_headers = {
                'X-Deining-Recalled-Memories': recalled_b64,
                'Access-Control-Expose-Headers': 'X-Deining-Recalled-Memories'
            }
            for key in ['Content-Type', 'Cache-Control', 'X-Accel-Buffering']:
                if key in resp.headers:
                    response_headers[key] = resp.headers[key]

            return Response(
                stream_with_context(generate()),
                status=resp.status_code,
                headers=response_headers,
                content_type=resp.headers.get('Content-Type', 'text/event-stream')
            )
        else:
            response_headers = {
                'X-Deining-Recalled-Memories': recalled_b64,
                'Access-Control-Expose-Headers': 'X-Deining-Recalled-Memories'
            }
            for key in ['Content-Type', 'Cache-Control']:
                if key in resp.headers:
                    response_headers[key] = resp.headers[key]

            return Response(
                resp.content,
                status=resp.status_code,
                headers=response_headers,
                content_type=resp.headers.get('Content-Type', 'application/json')
            )

    except http_requests.exceptions.ConnectionError:
        return jsonify({'error': 'LLM推論サーバーに接続できません (ポート8080)'}), 502
    except Exception as e:
        print(f"[WARN] プロキシエラー: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/v1/chat/completions/control', methods=['POST', 'OPTIONS'])
def proxy_chat_control():
    """llama-server の /v1/chat/completions/control への透過プロキシ（推論停止等）"""
    if request.method == 'OPTIONS':
        return '', 204
    try:
        resp = _http_session.post(
            f"{LLAMA_SERVER_URL}/v1/chat/completions/control",
            json=request.get_json(),
            headers={'Content-Type': 'application/json'},
            timeout=10
        )
        return Response(resp.content, status=resp.status_code, content_type='application/json')
    except Exception as e:
        print(f"[WARN] Control プロキシエラー: {e}")
        return jsonify({'error': str(e)}), 500

# ====== Speech-to-Text (STT) API ======
import tempfile

stt_model = None
stt_lock = threading.Lock()

def get_stt_model():
    global stt_model
    if stt_model is None:
        with stt_lock:
            if stt_model is None:
                from reazonspeech.k2.asr import load_model
                print("[INFO] Loading ReazonSpeech-K2 model...")
                stt_model = load_model(device="cpu")
                print("[INFO] ReazonSpeech-K2 model loaded successfully!")
    return stt_model

@app.route('/api/stt', methods=['POST', 'OPTIONS'])
def speech_to_text():
    if request.method == 'OPTIONS':
        return '', 204
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    MAX_STT_BYTES = 25 * 1024 * 1024  # 25MB 上限
    file.seek(0, os.SEEK_END)
    stt_file_size = file.tell()
    file.seek(0)
    if stt_file_size > MAX_STT_BYTES:
        return jsonify({'error': f'音声ファイルサイズが上限(25MB)を超過しています ({stt_file_size / (1024*1024):.1f}MB)'}), 413
        
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_audio:
            temp_path = temp_audio.name
            file.save(temp_path)

        print(f"[INFO] Received audio file for STT. Saved to {temp_path}")

        # Load model and transcribe on CPU
        model = get_stt_model()
        from reazonspeech.k2.asr import transcribe, audio_from_path

        audio = audio_from_path(temp_path)
        ret = transcribe(model, audio)

        print(f"[INFO] STT Transcription Result: {ret.text}")
        return jsonify({'text': ret.text})
    except Exception as e:
        print(f"[WARN] STT Error: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        # [S06 fix] 成否に関係なく一時 WAV を必ず除去する (失敗経路のディスクリーク防止)
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

# ====== Live2D アバターモデル自動検知＆ZIP解凍アップロード API ======
def patch_live2d_model_files(folder_path, model_id):
    """
    BOOTH等の元ZIPから展開されたLive2Dモデルファイルを、泥濘システムの表情トリガー・対話仕様に自動パッチする。
    - 茶髪 (brown / fav_usaneko_alwb):
      - 00.idle.exp3.json を全パラメータ安全リセット仕様に更新
      - 02.angry.exp3.json を怒り口（への字口Set）および笑顔無効化(Param80: 0, Set)仕様に更新
      - fav_usaneko_alwb.model3.json の Expressions 配列に idle, smile, angry, blush, cry, surprised 等を確実に登録
    - 銀髪 (silver / ulvm2_0001):
      - ulvm2_0001.model3.json の Expressions 配列に 12種類の表情（idle, angry, blush, cry, smile, surprised, etc.）を登録
      - animation/idle.exp3.json を実体ファイルとして安全生成
    """
    if not os.path.isdir(folder_path):
        return

    import json, glob

    anim_dir = os.path.join(folder_path, "animation")
    os.makedirs(anim_dir, exist_ok=True)

    if model_id == "brown":
        # 1. 02.angry.exp3.json の口・眉 Set 定義（への字口と笑顔パーツ無効化）
        angry_path = os.path.join(anim_dir, "02.angry.exp3.json")
        if os.path.exists(angry_path):
            try:
                with open(angry_path, 'r', encoding='utf-8') as f:
                    angry_data = json.load(f)
                params = angry_data.get("Parameters", [])
                
                key_updates = {
                    "ParamMouthForm": (-1.0, "Set"),
                    "Param80": (0.0, "Set"),
                    "ParamBrowLAngle": (-1.0, "Set"),
                    "ParamBrowRAngle": (-1.0, "Set")
                }
                for p in params:
                    pid = p.get("Id")
                    if pid in key_updates:
                        val, blend = key_updates[pid]
                        p["Value"] = val
                        p["Blend"] = blend
                        del key_updates[pid]
                for pid, (val, blend) in key_updates.items():
                    params.insert(0, {"Id": pid, "Value": val, "Blend": blend})
                
                angry_data["Parameters"] = params
                with open(angry_path, 'w', encoding='utf-8') as f:
                    json.dump(angry_data, f, indent=4, ensure_ascii=False)
                print(f"[Deining Live2D Patch] {angry_path} mouth/brow Set patch applied")
            except Exception as e:
                print(f"[Deining Live2D Patch] angry patch failed: {e}")

        # 2. fav_usaneko_alwb.model3.json の実在表情エイリアス登録（架空ファイルは一切登録しない）
        m3_candidates = glob.glob(os.path.join(folder_path, '*.model3.json'))
        for m3_path in m3_candidates:
            try:
                with open(m3_path, 'r', encoding='utf-8') as f:
                    m3_data = json.load(f)
                if "FileReferences" not in m3_data:
                    m3_data["FileReferences"] = {}
                if "Expressions" not in m3_data["FileReferences"] or not isinstance(m3_data["FileReferences"]["Expressions"], list):
                    m3_data["FileReferences"]["Expressions"] = []
                
                exps = m3_data["FileReferences"]["Expressions"]
                # 架空の idle や 00.idle は完全に除外
                exps = [e for e in exps if e.get("Name") not in ["idle", "00.idle", "18.smile"] and not (e.get("File") or "").endswith("00.idle.exp3.json")]
                
                existing_names = { (e.get("Name") or "").lower() for e in exps }
                
                # 実在するファイルへの安全なエイリアスのみ登録
                required_exps = [
                    {"Name": "angry", "File": "animation/02.angry.exp3.json"},
                    {"Name": "blush", "File": "animation/01.blush.exp3.json"},
                    {"Name": "cry", "File": "animation/03.cry.exp3.json"},
                    {"Name": "pale", "File": "animation/04.pale.exp3.json"},
                    {"Name": "surprised", "File": "animation/13.surprised.exp3.json"},
                    {"Name": "smile", "File": "animation/12.cat_mouth.exp3.json"},
                    {"Name": "xd", "File": "animation/08.XD.exp3.json"}
                ]
                for req in required_exps:
                    if req["Name"].lower() not in existing_names and os.path.exists(os.path.join(folder_path, req["File"])):
                        exps.append(req)
                        existing_names.add(req["Name"].lower())
                
                m3_data["FileReferences"]["Expressions"] = exps
                with open(m3_path, 'w', encoding='utf-8') as f:
                    json.dump(m3_data, f, indent=4, ensure_ascii=False)
                print(f"[Deining Live2D Patch] {m3_path} valid expressions registered")
            except Exception as e:
                print(f"[Deining Live2D Patch] model3.json patch failed: {e}")

    elif model_id == "silver":
        # 1. idle.exp3.json の存在保証
        idle_path = os.path.join(anim_dir, "idle.exp3.json")
        if not os.path.exists(idle_path):
            try:
                idle_data = {
                    "Type": "Live2D Expression",
                    "FadeInTime": 0.5,
                    "FadeOutTime": 0.5,
                    "Parameters": [
                        { "Id": "ParamEyeBallX", "Value": 0, "Blend": "Add" }
                    ]
                }
                with open(idle_path, 'w', encoding='utf-8') as f:
                    json.dump(idle_data, f, indent=2, ensure_ascii=False)
                print(f"[Deining Live2D Patch] {idle_path} created")
            except Exception as e:
                print(f"[Deining Live2D Patch] silver idle create failed: {e}")

        # 2. ulvm2_0001.model3.json の Expressions 12種類登録
        m3_candidates = glob.glob(os.path.join(folder_path, '*.model3.json'))
        for m3_path in m3_candidates:
            try:
                with open(m3_path, 'r', encoding='utf-8') as f:
                    m3_data = json.load(f)
                if "FileReferences" not in m3_data:
                    m3_data["FileReferences"] = {}
                if "Expressions" not in m3_data["FileReferences"] or not m3_data["FileReferences"]["Expressions"]:
                    m3_data["FileReferences"]["Expressions"] = [
                        { "Name": "idle", "File": "animation/idle.exp3.json" },
                        { "Name": "angry", "File": "animation/angry.exp3.json" },
                        { "Name": "blush", "File": "animation/blush.exp3.json" },
                        { "Name": "cry", "File": "animation/cry.exp3.json" },
                        { "Name": "smile", "File": "animation/smile.exp3.json" },
                        { "Name": "surprised", "File": "animation/surprised.exp3.json" },
                        { "Name": "sleep", "File": "animation/sleep.exp3.json" },
                        { "Name": "xd", "File": "animation/XD.exp3.json" },
                        { "Name": "heart", "File": "animation/fx_1_heart.exp3.json" },
                        { "Name": "sweat", "File": "animation/fx_4_sweat.exp3.json" },
                        { "Name": "question", "File": "animation/fx_7_question.exp3.json" },
                        { "Name": "angry_mark", "File": "animation/fx_13_angry.exp3.json" }
                    ]
                    with open(m3_path, 'w', encoding='utf-8') as f:
                        json.dump(m3_data, f, indent=4, ensure_ascii=False)
                    print(f"[Deining Live2D Patch] {m3_path} 12 expressions registered")
            except Exception as e:
                print(f"[Deining Live2D Patch] silver model3.json patch failed: {e}")


ALLOWED_LIVE2D_EXTENSIONS = (
    # Live2D Cubism 定義ファイル群
    '.moc3', '.model3.json', '.cdi3.json', '.physics3.json',
    '.pose3.json', '.exp3.json', '.motion3.json', '.userdata3.json',
    # モデル・VTube Studio設定・情報
    '.json', '.txt',
    # 画像アセット (テクスチャ・アバターアイコン)
    '.png', '.jpg', '.jpeg', '.webp',
    # モーション音声 (リップシンク・ボイス)
    '.wav', '.mp3', '.ogg'
)
MAX_UNCOMPRESSED_SIZE = 150 * 1024 * 1024  # 150MB
MAX_ZIP_ENTRIES = 300

def safe_copy_live2d_assets(src_dir, dst_dir):
    """ホワイトリストに登録された安全なLive2Dアセットのみをコピー（スクリプト・実行ファイル混入を完全防止）"""
    os.makedirs(dst_dir, exist_ok=True)
    copied_count = 0
    total_size = 0
    MAX_COPY_FILES = MAX_ZIP_ENTRIES
    MAX_COPY_SIZE = MAX_UNCOMPRESSED_SIZE

    for root, dirs, files in os.walk(src_dir):
        rel_root = os.path.relpath(root, src_dir)
        target_dir = os.path.join(dst_dir, rel_root) if rel_root != '.' else dst_dir
        for f in files:
            lower_name = f.lower()
            if not any(lower_name.endswith(ext) for ext in ALLOWED_LIVE2D_EXTENSIONS):
                print(f"[WARN] [Security] 非許可拡張子のためスキップ: {f}")
                continue

            src_file = os.path.join(root, f)
            f_size = os.path.getsize(src_file)
            copied_count += 1
            total_size += f_size
            if copied_count > MAX_COPY_FILES or total_size > MAX_COPY_SIZE:
                raise ValueError("展開ファイル数または合計容量が安全制限(150MB/300ファイル)を超過しました")

            os.makedirs(target_dir, exist_ok=True)
            shutil.copy2(src_file, os.path.join(target_dir, f))

def get_custom_public_dir() -> str:
    env_dir = os.environ.get('DEINEI_CUSTOM_PUBLIC_DIR', '').strip()
    if env_dir:
        return os.path.abspath(env_dir)
    return os.path.abspath(os.path.join(BASE_DIR, '..', '..', 'llama-b9873-bin-win-cuda-13.3-x64sandbox', 'custom_public'))

def get_static_dir() -> str:
    env_dir = os.environ.get('DEINEI_STATIC_DIR', '').strip()
    if env_dir:
        return os.path.abspath(env_dir)
    return os.path.abspath(os.path.join(BASE_DIR, '..', 'llama.cpp-b9873', 'tools', 'ui', 'static', 'deining'))

# ====== VRM 3Dモデル管理 定義・共通処理 ======
MAX_VRM_FILE_BYTES = 60 * 1024 * 1024       # 1ファイル 60MB (Gateway 64MB 上限の内側)
MAX_VRM_TOTAL_BYTES = 1024 * 1024 * 1024    # VRM 全体の合計保存容量 1GB

def get_vrm_models_dir() -> str:
    return os.path.join(get_custom_public_dir(), 'deining', 'vrm')

def get_all_vrm_models_data() -> list:
    vrm_dir = get_vrm_models_dir()
    static_dir = get_static_dir()

    # static_dir (ソースディレクトリ) から custom_public への自動同期・復元
    if static_dir and os.path.isdir(os.path.join(static_dir, 'vrm')):
        static_vrm_dir = os.path.join(static_dir, 'vrm')
        os.makedirs(vrm_dir, exist_ok=True)
        for item in os.listdir(static_vrm_dir):
            src_item = os.path.join(static_vrm_dir, item)
            dst_item = os.path.join(vrm_dir, item)
            if os.path.isdir(src_item) and not os.path.exists(dst_item):
                try:
                    shutil.copytree(src_item, dst_item)
                except Exception:
                    pass

    if not os.path.isdir(vrm_dir):
        return []
    models = []
    for item in sorted(os.listdir(vrm_dir)):
        meta_path = os.path.join(vrm_dir, item, 'meta.json')
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                vrm_file = os.path.join(vrm_dir, item, f"{meta.get('id', item)}.vrm")
                if os.path.isfile(vrm_file):
                    models.append(meta)
            except Exception:
                continue
    return models

def save_vrm_model_data(data: bytes, orig_name: str) -> tuple[bool, str, dict | None, bool]:
    """単一のVRMバイナリデータをバリデーション・メタ抽出・重複判定し、
    custom_public/deining/vrm/<model_id>/ 配下に保存する。
    戻り値: (success, message_or_error, model_record, is_duplicate)
    """
    if len(data) > MAX_VRM_FILE_BYTES:
        return False, f"ファイルサイズが上限(60MB)を超過しています ({len(data) / (1024*1024):.1f}MB)", None, False

    if len(data) < 12:
        return False, "ファイルデータが小さすぎるため有効なVRMではありません", None, False

    try:
        from vrm_meta import parse_vrm_meta, VrmMetaError
        meta_info = parse_vrm_meta(data)
    except VrmMetaError as e:
        return False, f"無効なVRMモデルです: {e}", None, False
    except Exception as e:
        return False, f"VRMの解析中にエラーが発生しました: {e}", None, False

    file_sha = hashlib.sha256(data).hexdigest()
    vrm_dir = get_vrm_models_dir()
    os.makedirs(vrm_dir, exist_ok=True)

    existing_models = get_all_vrm_models_data()
    for m in existing_models:
        if m.get('sha256') == file_sha:
            return True, f"同一のモデル「{m.get('name')}」が既に登録されています", m, True

    current_total_bytes = sum(m.get('fileSize', 0) for m in existing_models)
    if current_total_bytes + len(data) > MAX_VRM_TOTAL_BYTES:
        return False, f"VRMモデルの合計保存容量(1GB)を超過します (現在: {current_total_bytes / (1024*1024):.1f}MB, 追加: {len(data) / (1024*1024):.1f}MB)", None, False

    raw_name = (meta_info.get('name') or '').strip()
    stem_name = os.path.splitext(orig_name)[0].strip()

    variant_keywords = ['short', 'long', 'alt', 'v2', 'v3', 'ver', 'costume', 'cloth', 'sleeve', 'dress']
    if not raw_name:
        display_name = stem_name
    elif stem_name and raw_name.lower() != stem_name.lower() and (raw_name.lower() in stem_name.lower() or any(k in stem_name.lower() for k in variant_keywords)):
        display_name = stem_name
    else:
        display_name = raw_name

    safe_slug = re.sub(r'[^a-z0-9_-]', '', stem_name.lower())[:20]
    if not safe_slug:
        safe_slug = 'vrm'
    model_id = f"{safe_slug}_{file_sha[:12]}"

    target_model_dir = os.path.join(vrm_dir, model_id)
    os.makedirs(target_model_dir, exist_ok=True)

    target_vrm_path = os.path.join(target_model_dir, f"{model_id}.vrm")
    with open(target_vrm_path, 'wb') as f:
        f.write(data)

    model_record = {
        'id': model_id,
        'name': display_name,
        'kind': 'vrm',
        'format': meta_info.get('format', 'vrm1'),
        'authors': meta_info.get('authors', []),
        'license': meta_info.get('licenseUrl') or meta_info.get('licenseName') or '',
        'fileSize': len(data),
        'sha256': file_sha,
        'expressions': meta_info.get('expressionPresets', []),
        'customExpressions': meta_info.get('expressionCustom', []),
        'springJoints': meta_info.get('springJoints', 0),
        'url': f"/deining/vrm/{model_id}/{model_id}.vrm"
    }

    meta_json_path = os.path.join(target_model_dir, 'meta.json')
    with open(meta_json_path, 'w', encoding='utf-8') as f:
        json.dump(model_record, f, ensure_ascii=False, indent=2)

    # 配置先2: static ディレクトリ (存在する場合同期し、UI再ビルド時にも保持)
    static_dir = get_static_dir()
    if static_dir and os.path.isdir(static_dir):
        static_vrm_model_dir = os.path.join(static_dir, 'vrm', model_id)
        try:
            os.makedirs(static_vrm_model_dir, exist_ok=True)
            with open(os.path.join(static_vrm_model_dir, f"{model_id}.vrm"), 'wb') as f:
                f.write(data)
            with open(os.path.join(static_vrm_model_dir, 'meta.json'), 'w', encoding='utf-8') as f:
                json.dump(model_record, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[WARN] static_dir へのVRM同期をスキップしました: {e}")

    print(f"[INFO] VRMモデル配置成功: {model_record['name']} (ID: {model_id}) -> {model_record['url']}")
    return True, f"VRMモデル「{display_name}」を配置しました", model_record, False

def extract_avatar_zip(zip_source, custom_public_dir, static_dir=None):
    """
    ZIPファイルを受け取り、解凍・探索を行って自動判定する:
    1. ZIP内に .vrm ファイルが1件以上ある場合:
       → 見つかった全ての VRM モデルを一括登録 (3Dアバター)
    2. .model3.json がある場合:
       → 専用Live2Dモデル2体（銀髪/茶髪）を判定して配置 (2Dアバター)
       → 専用2体以外の場合は親切にVRM利用を促すガイダンスを返却
    3. どちらも見つからない場合:
       → エラーメッセージを返却
    """
    import zipfile, tempfile, shutil, glob

    with tempfile.TemporaryDirectory() as td:
        temp_zip_path = os.path.join(td, 'upload.zip')
        if hasattr(zip_source, 'read'):
            zip_source.seek(0)
            with open(temp_zip_path, 'wb') as f:
                shutil.copyfileobj(zip_source, f)
        elif isinstance(zip_source, str) and os.path.isfile(zip_source):
            shutil.copyfile(zip_source, temp_zip_path)
        else:
            return False, "無効なアップロードソースです", None

        # 1. 最初の解凍（ZipBombサイズチェック付き）
        try:
            with zipfile.ZipFile(temp_zip_path) as z:
                total_uncompressed = sum(info.file_size for info in z.infolist())
                total_entries = len(z.infolist())
                if total_uncompressed > MAX_UNCOMPRESSED_SIZE or total_entries > MAX_ZIP_ENTRIES:
                    return False, f"ZIPファイルの容量またはファイル数が制限(150MB/300ファイル)を超過しています (解凍サイズ: {total_uncompressed / (1024*1024):.1f}MB, ファイル数: {total_entries})", None
                z.extractall(td)
        except Exception as e:
            return False, f"ZIPファイルの解凍に失敗しました: {e}", None

        # 2. ネストされたZIPファイルの解凍 (BOOTH二重ZIP対応: ネスト分も容量・ファイル数に合算計上)
        nested_zips = [
            nz for nz in glob.glob(os.path.join(td, '**', '*.zip'), recursive=True)
            if os.path.abspath(nz) != os.path.abspath(temp_zip_path)
        ]
        if nested_zips:
            for nz in nested_zips:
                try:
                    with zipfile.ZipFile(nz) as z2:
                        nz_uncompressed = sum(info.file_size for info in z2.infolist())
                        nz_entries = len(z2.infolist())
                        total_uncompressed += nz_uncompressed
                        total_entries += nz_entries
                        if total_uncompressed > MAX_UNCOMPRESSED_SIZE or total_entries > MAX_ZIP_ENTRIES:
                            return False, f"ネストされたZIPを含めた容量またはファイル数が制限(150MB/300ファイル)を超過しています (合計サイズ: {total_uncompressed / (1024*1024):.1f}MB, 合計ファイル数: {total_entries})", None
                        nz_dir = os.path.splitext(nz)[0]
                        os.makedirs(nz_dir, exist_ok=True)
                        z2.extractall(nz_dir)
                except Exception as e:
                    return False, f"ネストされたZIPの解凍に失敗しました: {e}", None
                try:
                    os.remove(nz)
                except Exception:
                    pass

        # 3. .vrm ファイルの探索 (3Dアバタールート)
        vrm_files = []
        for root, _, files in os.walk(td):
            for f in files:
                if f.lower().endswith('.vrm'):
                    vrm_files.append(os.path.join(root, f))

        if vrm_files:
            vrm_files.sort(key=lambda p: (os.path.relpath(p, td).count(os.sep), os.path.basename(p)))
            print(f"[INFO] ZIP内から {len(vrm_files)} 件のVRMモデルを検出しました: {[os.path.basename(p) for p in vrm_files]}")

            registered_models = []
            duplicate_models = []
            errors = []

            for vp in vrm_files:
                fname = os.path.basename(vp)
                try:
                    with open(vp, 'rb') as f:
                        vrm_data = f.read()
                    ok, msg, m_record, is_dup = save_vrm_model_data(vrm_data, fname)
                    if ok and m_record:
                        if is_dup:
                            duplicate_models.append(m_record)
                        else:
                            registered_models.append(m_record)
                    else:
                        errors.append(f"{fname}: {msg}")
                except Exception as e:
                    errors.append(f"{fname}: {e}")

            if registered_models:
                primary = registered_models[0]
                if len(registered_models) == 1:
                    msg = f"3Dモデル「{primary['name']}」を登録しました"
                else:
                    names_str = ", ".join(m['name'] for m in registered_models)
                    msg = f"{len(registered_models)}体の3Dモデルを一括登録しました ({names_str})"
                return True, msg, {
                    'kind': 'vrm',
                    'model': primary,
                    'registered_models': registered_models,
                    'duplicate_models': duplicate_models,
                    'duplicate': False
                }
            elif duplicate_models:
                primary = duplicate_models[0]
                names_str = ", ".join(m['name'] for m in duplicate_models)
                msg = f"同封の3Dモデルは全て登録済みです ({names_str})"
                return True, msg, {
                    'kind': 'vrm',
                    'model': primary,
                    'registered_models': duplicate_models,
                    'duplicate_models': duplicate_models,
                    'duplicate': True
                }
            else:
                err_summary = "; ".join(errors) if errors else "有効なVRMモデルデータを読み込めませんでした"
                return False, f"VRMモデルの登録に失敗しました: {err_summary}", None

        # 4. .model3.json の探索 (2D Live2Dアバタールート)
        model3_files = []
        for root, _, files in os.walk(td):
            for f in files:
                if f.lower().endswith('.model3.json'):
                    model3_files.append(os.path.join(root, f))

        if model3_files:
            def sort_key(p):
                rel = os.path.relpath(p, td)
                depth = rel.count(os.sep)
                p_name = os.path.splitext(os.path.basename(p))[0]
                d_name = os.path.basename(os.path.dirname(p))
                match_bonus = 0 if p_name == d_name else 1
                return (match_bonus, depth, len(rel))

            model3_files.sort(key=sort_key)
            chosen_model3 = model3_files[0]
            model_source_dir = os.path.dirname(chosen_model3)
            model3_filename = os.path.basename(chosen_model3)
            folder_basename = os.path.basename(model_source_dir)

            print(f"[INFO] Live2Dモデル検出: {chosen_model3}")

            lower_str = (chosen_model3 + " " + folder_basename).lower()
            if "ulvm2" in lower_str or "銀髪" in lower_str:
                model_id = "silver"
                model_name = "銀髪の女の子"
                dest_folder_name = "ulvm2_0001"
            elif "usaneko" in lower_str or "茶髪" in lower_str:
                model_id = "brown"
                model_name = "茶髪の癒し系お姉さん"
                dest_folder_name = "fav_usaneko_alwb"
            else:
                return False, "対応外のLive2Dモデルです。Live2Dは規約上、公式専用モデル（銀髪の女の子 / 茶髪の癒し系お姉さん）のみに対応しています。お好みのオリジナルアバターを使いたい場合は、3Dアバター（.vrm形式 または VRM内包ZIP）をご利用ください。", None

            target_root_dir = os.path.join(custom_public_dir, 'deining', dest_folder_name)
            try:
                safe_copy_live2d_assets(model_source_dir, target_root_dir)
                patch_live2d_model_files(target_root_dir, model_id)
            except Exception as e:
                return False, f"アセット配置中にエラーが発生しました: {e}", None

            if static_dir and os.path.isdir(static_dir):
                target_static_dir = os.path.join(static_dir, dest_folder_name)
                try:
                    safe_copy_live2d_assets(model_source_dir, target_static_dir)
                    patch_live2d_model_files(target_static_dir, model_id)
                except Exception:
                    pass

            rel_url = f"/deining/{dest_folder_name}/{model3_filename}"
            print(f"[INFO] Live2Dモデル配置成功: {model_name} -> {rel_url}")

            model_obj = {
                'id': model_id,
                'name': model_name,
                'url': rel_url,
                'exists': True
            }
            return True, f"Live2Dモデル「{model_name}」を配置しました", {
                'kind': 'live2d',
                'model': model_obj,
                'registered_models': [model_obj]
            }

        return False, "有効なアバターが見つかりませんでした。VRMファイル(.vrm / ZIP)または専用Live2DのZIPをご確認ください。", None

def extract_live2d_zip(zip_source, custom_public_dir, static_dir=None):
    """下位互換ラッパー: extract_avatar_zip を呼び出す"""
    return extract_avatar_zip(zip_source, custom_public_dir, static_dir)

def get_all_avatar_models_data():
    import glob
    custom_public_dir = get_custom_public_dir()
    
    def find_model3_json(folder_name, default_fallback):
        # 1. 統一パス: custom_public/deining/<folder>/ 配下を検索
        direct_dir = os.path.join(custom_public_dir, 'deining', folder_name)
        if os.path.isdir(direct_dir):
            matches = glob.glob(os.path.join(direct_dir, '**', '*.model3.json'), recursive=True)
            if matches:
                rel_path = os.path.relpath(matches[0], custom_public_dir).replace('\\', '/')
                return '/' + rel_path, True

        # 2. modelsサブフォルダ配置互換: custom_public/deining/models/<folder>/ 配下を検索
        jp_dir = os.path.join(custom_public_dir, 'deining', 'models', folder_name)
        if os.path.isdir(jp_dir):
            matches = glob.glob(os.path.join(jp_dir, '**', '*.model3.json'), recursive=True)
            if matches:
                rel_path = os.path.relpath(matches[0], custom_public_dir).replace('\\', '/')
                return '/' + rel_path, True
        
        # 3. 旧パス互換: custom_public/<default_fallback> の存在確認
        old_path = os.path.join(custom_public_dir, default_fallback.lstrip('/\\').replace('/', os.sep))
        if os.path.exists(old_path):
            return default_fallback, True
            
        return default_fallback, False

    silver_url, silver_exists = find_model3_json('ulvm2_0001', '/deining/ulvm2_0001/ulvm2_0001.model3.json')
    if not silver_exists:
        silver_url, silver_exists = find_model3_json('銀髪の女の子', '/deining/ulvm2_0001/ulvm2_0001.model3.json')

    brown_url, brown_exists = find_model3_json('fav_usaneko_alwb', '/deining/fav_usaneko_alwb/fav_usaneko_alwb.model3.json')
    if not brown_exists:
        brown_url, brown_exists = find_model3_json('茶髪の癒し系お姉さん', '/deining/fav_usaneko_alwb/fav_usaneko_alwb.model3.json')

    return [
        {
            'id': 'silver',
            'name': '銀髪の女の子',
            'url': silver_url,
            'exists': silver_exists
        },
        {
            'id': 'brown',
            'name': '茶髪の癒し系お姉さん',
            'url': brown_url,
            'exists': brown_exists
        }
    ]

@app.route('/api/avatar/models', methods=['GET'])
def get_avatar_models():
    return jsonify({'models': get_all_avatar_models_data()})

@app.route('/api/avatar/upload', methods=['POST'])
def upload_avatar_model():
    """Live2DモデルまたはVRMモデル（.vrm / .zip）を受け取り、自動判別・解凍・配置するAPI"""
    custom_public_dir = get_custom_public_dir()
    static_dir = get_static_dir()

    if 'file' in request.files:
        file_obj = request.files['file']
        if not file_obj or file_obj.filename == '':
            return jsonify({'error': 'ファイルが選択されていません'}), 400
        
        filename = file_obj.filename or 'upload'
        if filename.lower().endswith('.vrm'):
            data = file_obj.read()
            ok, msg, model_record, is_dup = save_vrm_model_data(data, filename)
            if not ok:
                return jsonify({'error': msg}), 400
            return jsonify({
                'success': True,
                'ok': True,
                'kind': 'vrm',
                'duplicate': is_dup,
                'message': msg if is_dup else f"VRMモデル「{model_record['name']}」を配置しました",
                'model': model_record,
                'registered_models': [model_record],
                'models': get_all_avatar_models_data(),
                'vrm_models': get_all_vrm_models_data()
            }), 200
        elif filename.lower().endswith('.zip'):
            success, msg, result = extract_avatar_zip(file_obj, custom_public_dir, static_dir)
            if not success:
                return jsonify({'error': msg}), 400
            
            kind = result.get('kind', 'live2d') if isinstance(result, dict) else 'live2d'
            primary_model = result.get('model') if isinstance(result, dict) and 'model' in result else result
            registered_models = result.get('registered_models', [primary_model]) if isinstance(result, dict) else [result]
            
            return jsonify({
                'success': True,
                'ok': True,
                'kind': kind,
                'duplicate': result.get('duplicate', False) if isinstance(result, dict) else False,
                'message': msg,
                'model': primary_model,
                'registered_models': registered_models,
                'models': get_all_avatar_models_data(),
                'vrm_models': get_all_vrm_models_data()
            }), 200
        else:
            return jsonify({'error': '対応していないファイル形式です。.vrm ファイルまたはモデルが格納された .zip ファイルを指定してください'}), 400

    return jsonify({'error': '有効なファイルがアップロードされていません'}), 400

@app.route('/api/avatar/vrm/list', methods=['GET'])
def list_vrm_models():
    """配置済みのVRMモデル一覧(カタログ)を返す"""
    return jsonify({'models': get_all_vrm_models_data()})

@app.route('/api/avatar/vrm/upload', methods=['POST'])
def upload_vrm_model():
    """VRMファイル（.vrm）または VRM内包ZIPを受け取り、バリデーション・メタデータ抽出を行い配置する"""
    cl = request.content_length or request.headers.get('Content-Length')
    if cl is not None:
        try:
            if int(cl) > MAX_VRM_FILE_BYTES:
                return jsonify({'error': f'ファイルサイズが上限(60MB)を超過しています ({int(cl) / (1024*1024):.1f}MB)'}), 413
        except ValueError:
            pass

    if 'file' not in request.files:
        return jsonify({'error': 'ファイルが選択されていません'}), 400

    file_obj = request.files['file']
    if not file_obj or file_obj.filename == '':
        return jsonify({'error': 'ファイル名が無効です'}), 400

    orig_name = file_obj.filename or 'model.vrm'
    if orig_name.lower().endswith('.zip'):
        custom_public_dir = get_custom_public_dir()
        static_dir = get_static_dir()
        success, msg, result = extract_avatar_zip(file_obj, custom_public_dir, static_dir)
        if not success:
            return jsonify({'error': msg}), 400
        primary_model = result.get('model') if isinstance(result, dict) and 'model' in result else result
        registered_models = result.get('registered_models', [primary_model]) if isinstance(result, dict) else [result]
        return jsonify({
            'ok': True,
            'success': True,
            'kind': result.get('kind', 'vrm'),
            'duplicate': result.get('duplicate', False) if isinstance(result, dict) else False,
            'message': msg,
            'model': primary_model,
            'registered_models': registered_models,
            'models': get_all_vrm_models_data()
        }), 200

    if not orig_name.lower().endswith('.vrm'):
        return jsonify({'error': '拡張子が .vrm または .zip のファイルのみアップロード可能です'}), 400

    data = file_obj.read()
    ok, msg, model_record, is_dup = save_vrm_model_data(data, orig_name)
    if not ok:
        status_code = 413 if '60MB' in msg or '上限' in msg else 400
        return jsonify({'error': msg}), status_code

    all_models = get_all_vrm_models_data()
    return jsonify({
        'ok': True,
        'success': True,
        'duplicate': is_dup,
        'message': msg if is_dup else f"VRMモデル「{model_record['name']}」を配置しました",
        'model': model_record,
        'registered_models': [model_record],
        'models': all_models
    }), 200

@app.route('/api/avatar/vrm/<string:model_id>', methods=['DELETE'])
def delete_vrm_model(model_id):
    """登録済みの VRM モデルを物理削除する"""
    if not model_id or not re.match(r'^[a-zA-Z0-9_-]+$', model_id):
        return jsonify({'error': '無効なモデルIDです'}), 400

    vrm_dir = get_vrm_models_dir()
    target_model_dir = os.path.join(vrm_dir, model_id)

    # パストラバーサル防止確認
    if not os.path.abspath(target_model_dir).startswith(os.path.abspath(vrm_dir)):
        return jsonify({'error': '不正なパスアクセスです'}), 400

    if not os.path.isdir(target_model_dir):
        return jsonify({'error': '指定されたモデルが見つかりません'}), 404

    try:
        shutil.rmtree(target_model_dir, ignore_errors=False)
        static_dir = get_static_dir()
        if static_dir and os.path.isdir(static_dir):
            static_target = os.path.join(static_dir, 'vrm', model_id)
            if os.path.isdir(static_target):
                try:
                    shutil.rmtree(static_target, ignore_errors=True)
                except Exception:
                    pass
        print(f"[INFO] VRMモデルを削除しました: {model_id}")
        return jsonify({'ok': True, 'deleted': model_id, 'models': get_all_vrm_models_data()}), 200
    except Exception as e:
        return jsonify({'error': f'モデル削除中にエラーが発生しました: {e}'}), 500


if __name__ == '__main__':
    print('================================================================')
    print('[INFO] 泥濘(でいねい) プロキシサーバー起動中: http://127.0.0.1:5000')
    print('   → LLM推論サーバー (ポート8080) へプロキシします')
    print('   → RAG (ChromaDB) / Web検索 を自動注入します')
    print('   → STT (ReazonSpeech-K2) を遅延起動モード（初回入力時にロード）で待機中')
    print('   → [Security] Host検査 & CSRF検査 有効 (127.0.0.1 バインド)')
    print('================================================================')
    try:
        app.run(host=BIND_HOST, port=SERVER_PORT, threaded=True)
    except OSError as e:
        if getattr(e, 'winerror', None) == 10048 or 'address already in use' in str(e).lower():
            print("\n[ERROR] [ポート競合エラー] ポート 5000 が既に他のプロセスで使用されています！")
            print("   前回のサーバープロセスがバックグラウンドに残っている可能性があります。")
            print("   start_ai_servers.bat を再実行すると自動クリーンアップされます。\n")
        else:
            raise

