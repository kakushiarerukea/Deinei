import hashlib
import math
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

_TEST_SANDBOX_DIR = tempfile.mkdtemp(prefix="deinei_server_test_")
os.environ["DEINEI_DATA_DIR"] = os.path.join(_TEST_SANDBOX_DIR, "data")
os.environ["DEINEI_CHROMA_DIR"] = os.path.join(_TEST_SANDBOX_DIR, "chroma_db")
os.environ["DEINEI_SKIP_EMBED_LOAD"] = "1"
os.makedirs(os.environ["DEINEI_DATA_DIR"], exist_ok=True)
os.makedirs(os.environ["DEINEI_CHROMA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", "data")))
import server


class _Deterministic768Model:
    """テスト用の決定論的768次元エンコーダ（重みロード不要でコサイン類似度を再現）。"""

    def encode(self, texts, convert_to_numpy=True, convert_to_tensor= False):
        single = isinstance(texts, str)
        items = [texts] if single else list(texts)
        vecs = []
        for raw in items:
            clean = raw
            for prefix in ("文章: ", "クエリ: "):
                if clean.startswith(prefix):
                    clean = clean[len(prefix):]
            vec = [0.0] * 768
            for ch in clean:
                slot = (ord(ch) * 31) % 768
                vec[slot] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vec = [v / norm for v in vec]
            vecs.append(vec)
        if single:
            return vecs[0]
        return vecs


class TestServerMemoryAndApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mock_model = _Deterministic768Model()
        if server.ruri_ef is not None:
            server.ruri_ef.model = cls.mock_model
            server.ruri_ef._load_failed = False
        cls.client = server.app.test_client()
        cls.headers = {
            "Host": f"127.0.0.1:{server.SERVER_PORT}",
            "Origin": f"http://127.0.0.1:{server.LLAMA_PORT}",
        }

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TEST_SANDBOX_DIR, ignore_errors=True)

    def setUp(self):
        if server.ruri_ef is not None:
            server.ruri_ef.model = self.mock_model
            server.ruri_ef._load_failed = False

    def test_01_unloaded_embedding_does_not_persist_zero_vector(self):
        """埋め込みモデル未ロード時にゼロベクトルがChromaDBへ永続保存されないことを検証。"""
        server.ruri_ef.model = None
        server.ruri_ef._load_failed = True
        with self.assertRaises(RuntimeError):
            server.ruri_ef(["テスト記憶"])

        resp = self.client.post(
            "/memory/add?session_id=test_unloaded",
            json={"texts": ["ゼロベクトル防止テスト"]},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 503)

    def test_02_fact_and_parent_child_isolation(self):
        """親子リトリーバル(parent_child)の子チャンクがFact記憶の重複判定や検索に混入しないことを検証。"""
        sid = "test_isolation_session"
        r_pc = self.client.post(
            "/memory/add_parent_child",
            json={
                "session_id": sid,
                "message_id": "msg_10",
                "parent_text": "ユーザーとカフェでコーヒーを飲みながら読書について語り合ったエピソード。",
                "child_texts": ["ユーザーはブラックコーヒーが好き"],
            },
            headers=self.headers,
        )
        self.assertEqual(r_pc.status_code, 200)

        # 同一文言のFact記憶を登録した際、parent_childに吸収されず新規Factとして追加されること
        r_fact = self.client.post(
            f"/memory/add?session_id={sid}",
            json={
                "texts": ["ユーザーはブラックコーヒーが好き"],
                "message_id": "msg_10",
                "active_message_ids": ["msg_10"],
            },
            headers=self.headers,
        )
        self.assertEqual(r_fact.status_code, 200)
        data = r_fact.get_json()
        self.assertEqual(data.get("added"), 1)
        self.assertEqual(data.get("updated"), 0)

        # /memory/search では同一テキストを持つ parent_child が混入せず fact のみ 1 件が返ること
        r_search = self.client.post(
            "/memory/search",
            json={"session_id": sid, "query": "ユーザーはブラックコーヒーが好き", "n_results": "5"},
            headers=self.headers,
        )
        self.assertEqual(r_search.status_code, 200)
        memories = r_search.get_json().get("memories", [])
        self.assertEqual(len(memories), 1)

    def test_03_prune_respects_updated_at(self):
        """作成日が古くても updated_at が新しい記憶は prune で削除されないことを検証。"""
        sid = "test_prune_session"
        col = server.get_memory_collection(sid)
        old_iso = (datetime.now() - timedelta(days=200)).isoformat()
        recent_iso = (datetime.now() - timedelta(days=5)).isoformat()

        col.add(
            ids=["fact_reconfirmed", "fact_truly_stale"],
            documents=["再確認された大切な事実記憶", "200日間一度も触れられていない事実記憶"],
            metadatas=[
                {
                    "type": "fact",
                    "channel_id": "web_ui",
                    "timestamp": old_iso,
                    "created_at": old_iso,
                    "updated_at": recent_iso,
                },
                {
                    "type": "fact",
                    "channel_id": "web_ui",
                    "timestamp": old_iso,
                    "created_at": old_iso,
                    "updated_at": old_iso,
                },
            ],
        )

        r_prune = self.client.post(
            "/memory/prune",
            json={"session_id": sid, "max_age_days": "180"},
            headers=self.headers,
        )
        self.assertEqual(r_prune.status_code, 200)
        prune_data = r_prune.get_json()
        self.assertEqual(prune_data.get("deleted"), 1)

        remaining = col.get()
        self.assertIn("fact_reconfirmed", remaining["ids"])
        self.assertNotIn("fact_truly_stale", remaining["ids"])

    def test_04_parent_child_empty_message_id_no_collision(self):
        """message_id が空の複数エピソード登録時にID衝突で上書き消失しないことを検証。"""
        sid = "test_pc_empty_msgid"
        self.client.post(
            "/memory/add_parent_child",
            json={
                "session_id": sid,
                "message_id": "",
                "parent_text": "春の京都へ旅行に行ったエピソード。",
                "child_texts": ["京都旅行の思い出"],
            },
            headers=self.headers,
        )
        self.client.post(
            "/memory/add_parent_child",
            json={
                "session_id": sid,
                "message_id": "",
                "parent_text": "秋の北海道で海鮮を食べたエピソード。",
                "child_texts": ["北海道旅行の思い出"],
            },
            headers=self.headers,
        )
        col = server.get_memory_collection(sid)
        self.assertEqual(col.count(), 2)

    def test_04b_parent_child_same_message_id_two_blocks_no_overwrite(self):
        """16ブロック到達時に同一 message_id で先頭2ブロックが連続登録されても1ブロック目が上書き削除されないことを検証。"""
        sid = "test_pc_sliding_two_blocks"
        msg_id = "msg_sliding_boundary"
        block1_parent = "第1ブロックの要約テキスト。海辺のカフェで紅茶を飲んだ。"
        block2_parent = "第2ブロックの要約テキスト。夜の図書館で古い地図を調べた。"

        r1 = self.client.post(
            "/memory/add_parent_child",
            json={
                "session_id": sid,
                "message_id": msg_id,
                "parent_text": block1_parent,
                "child_texts": ["海辺のカフェ", "紅茶"],
            },
            headers=self.headers,
        )
        self.assertEqual(r1.status_code, 200)

        r2 = self.client.post(
            "/memory/add_parent_child",
            json={
                "session_id": sid,
                "message_id": msg_id,
                "parent_text": block2_parent,
                "child_texts": ["夜の図書館", "古い地図"],
            },
            headers=self.headers,
        )
        self.assertEqual(r2.status_code, 200)

        col = server.get_memory_collection(sid)
        # 1ブロック目(2件)と2ブロック目(2件)の計4件が両方保持されていること
        self.assertEqual(col.count(), 4)
        stored = col.get()
        stored_parents = {m.get("parent_text") for m in stored["metadatas"]}
        self.assertIn(block1_parent, stored_parents)
        self.assertIn(block2_parent, stored_parents)

        # 同一 parent_text (block1) の再送時は block2 を維持したまま block1 のみ冪等更新されること
        r1_retry = self.client.post(
            "/memory/add_parent_child",
            json={
                "session_id": sid,
                "message_id": msg_id,
                "parent_text": block1_parent,
                "child_texts": ["海辺のカフェ"],
            },
            headers=self.headers,
        )
        self.assertEqual(r1_retry.status_code, 200)
        self.assertEqual(col.count(), 3)

    def test_05_search_cache_ttl_eviction(self):
        """期限切れの _SEARCH_CACHE エントリが自動パージされることを検証。"""
        server._SEARCH_CACHE.clear()
        expired_ts = server.time.time() - (server._CACHE_TTL_SECONDS + 10)
        server._SEARCH_CACHE["stale_query"] = {"results": ["old"], "timestamp": expired_ts}
        server._SEARCH_CACHE["fresh_query"] = {"results": ["new"], "timestamp": server.time.time()}

        r_status = self.client.get("/api/search/status", headers=self.headers)
        self.assertEqual(r_status.status_code, 200)
        self.assertEqual(r_status.get_json().get("cache_entries"), 1)
        self.assertNotIn("stale_query", server._SEARCH_CACHE)
        self.assertIn("fresh_query", server._SEARCH_CACHE)

    def test_06_non_dict_json_payloads_do_not_crash_500(self):
        """配列や文字列などの非オブジェクトJSON送信時に500 AttributeErrorにならないことを検証。"""
        for path, method in [
            ("/memory/update", "PUT"),
            ("/search", "POST"),
            ("/v1/chat/completions", "POST"),
            ("/memory/add", "POST"),
            ("/memory/search", "POST"),
        ]:
            fn = getattr(self.client, method.lower())
            resp = fn(path, json=["not", "a", "dict"], headers=self.headers)
            self.assertLess(resp.status_code, 500, f"{method} {path} returned 500 on list JSON")

    def test_07_health_check_reflects_embedding_model_state(self):
        """/health が Ruri 埋め込みモデル未ロード時に 503、ロード失敗時に 500、完了時に 200 を返すことを検証。"""
        orig_skip = os.environ.get("DEINEI_SKIP_EMBED_LOAD")
        orig_model = server.ruri_ef.model
        orig_failed = server.ruri_ef._load_failed
        orig_err = getattr(server.ruri_ef, "_last_error", None)
        try:
            os.environ["DEINEI_SKIP_EMBED_LOAD"] = "0"

            # 1. ロード中 (model=None, _load_failed=False) -> 503
            server.ruri_ef.model = None
            server.ruri_ef._load_failed = False
            r_loading = self.client.get("/health", headers={"Origin": "null"})
            self.assertEqual(r_loading.status_code, 503)
            self.assertEqual(r_loading.get_json().get("status"), "loading")
            self.assertEqual(r_loading.headers.get("Access-Control-Allow-Origin"), "null")

            # 2. ロード失敗 (_load_failed=True) -> 500
            server.ruri_ef._load_failed = True
            server.ruri_ef._last_error = "os error 1455"
            r_err = self.client.get("/health", headers={"Origin": "null"})
            self.assertEqual(r_err.status_code, 500)
            self.assertEqual(r_err.get_json().get("status"), "error")
            self.assertIn("1455", r_err.get_json().get("error", ""))
            self.assertEqual(r_err.headers.get("Access-Control-Allow-Origin"), "null")

            # 3. ロード完了 (model is not None, _load_failed=False) -> 200
            server.ruri_ef._load_failed = False
            server.ruri_ef.model = orig_model
            r_ok = self.client.get("/health", headers={"Origin": "null"})
            self.assertEqual(r_ok.status_code, 200)
            self.assertEqual(r_ok.get_json().get("status"), "ok")
            self.assertEqual(r_ok.headers.get("Access-Control-Allow-Origin"), "null")
        finally:
            if orig_skip is None:
                os.environ.pop("DEINEI_SKIP_EMBED_LOAD", None)
            else:
                os.environ["DEINEI_SKIP_EMBED_LOAD"] = orig_skip
            server.ruri_ef.model = orig_model
            server.ruri_ef._load_failed = orig_failed
            server.ruri_ef._last_error = orig_err

    def test_ephemeral_web_dedup_and_prune(self):
        """即席Web記憶の同一クエリ再登録での重複排除(upsert)および期限切れ自動削除(prune)の検証"""
        import time
        init_count = server._web_ephemeral_collection.count()
        query = "東京 天気 テスト"
        results = ["【東京の天気】晴れ 気温20度 降水確率0%"]

        # 1. 初回登録
        server._register_ephemeral_web_retrieval(query, results, session_id="test_ephem_sid")
        c1 = server._web_ephemeral_collection.count()
        self.assertGreater(c1, init_count)

        # 2. キャッシュヒット等による再登録で件数が増加しないこと (upsert重複排除)
        server._register_ephemeral_web_retrieval(query, results, session_id="test_ephem_sid")
        c2 = server._web_ephemeral_collection.count()
        self.assertEqual(c1, c2, "同一スニペットの再登録でdoc件数が増加してはならない")

        # 3. 期限切れレコードの強制設定と prune の動作検証
        # テスト用レコードの expires_ts を過去時刻に更新
        docs = server._web_ephemeral_collection.get(where={"session_id": "test_ephem_sid"})
        self.assertTrue(len(docs["ids"]) > 0)
        expired_metas = []
        for m in docs["metadatas"]:
            m_copy = dict(m)
            m_copy["expires_ts"] = time.time() - 100.0
            expired_metas.append(m_copy)
        server._web_ephemeral_collection.update(
            ids=docs["ids"],
            metadatas=expired_metas
        )

        # prune 実行で期限切れが削除されること
        server._prune_ephemeral_web()
        docs_after = server._web_ephemeral_collection.get(where={"session_id": "test_ephem_sid"})
        self.assertEqual(len(docs_after["ids"]), 0, "期限切れの即席Web記憶が物理削除されること")


    def test_max_content_length_configured(self):
        self.assertEqual(server.app.config.get('MAX_CONTENT_LENGTH'), 256 * 1024 * 1024)

    def test_stt_cleanup_on_error(self):
        import tempfile
        from io import BytesIO
        tmp_dir = tempfile.gettempdir()
        before_wavs = {f for f in os.listdir(tmp_dir) if f.startswith("tmp") and f.endswith(".wav")}

        fake_wav = BytesIO(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00")
        response = self.client.post('/api/stt', data={'file': (fake_wav, 'test.wav')}, content_type='multipart/form-data')

        after_wavs = {f for f in os.listdir(tmp_dir) if f.startswith("tmp") and f.endswith(".wav")}
        leaked = after_wavs - before_wavs
        self.assertEqual(len(leaked), 0, f"STT エラー時に一時ファイルが残留している: {leaked}")

    def test_step1_kv_slot_lazy_save_restore(self):
        """16GB/24GBプリセット時のみKVキャッシュ退避・遅延復元が動作し、連続内部推論でsaveが1回に抑制されることを検証"""
        from unittest.mock import MagicMock, patch

        orig_profile_path = server._PROFILE_BAT_PATH
        tmp_profile = os.path.join(_TEST_SANDBOX_DIR, "test_deining_profile.bat")
        server._PROFILE_BAT_PATH = tmp_profile
        try:
            # 1. 12GB プリセット時は無効
            with open(tmp_profile, "w", encoding="utf-8") as f:
                f.write("@echo off\nset PROFILE_NAME=12GB (Medium)\n")
            self.assertFalse(server._is_step1_kv_preset_enabled())
            server._mark_main_chat_kv_active()
            with patch.object(server._http_session, "post") as mock_post:
                self.assertFalse(server._save_main_chat_kv_if_needed("internal_summary"))
                mock_post.assert_not_called()

            # 2. 16GB プリセット時は有効 & 連続3回の内部推論で save は最初の1回のみ
            with open(tmp_profile, "w", encoding="utf-8") as f:
                f.write("@echo off\nset PROFILE_NAME=16GB (High)\n")
            self.assertTrue(server._is_step1_kv_preset_enabled())
            server._mark_main_chat_kv_active()

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = b'{"n_saved": 1200, "n_restored": 1200, "timings": {"save_ms": 45.0, "restore_ms": 38.0}}'
            mock_resp.json.return_value = {
                "n_saved": 1200,
                "n_restored": 1200,
                "timings": {"save_ms": 45.0, "restore_ms": 38.0},
            }

            with patch.object(server._http_session, "post", return_value=mock_resp) as mock_post:
                # 要約 -> 事実抽出 -> 子要素抽出 -> Stage 1 検索 の4連続内部推論をシミュレート
                self.assertTrue(server._save_main_chat_kv_if_needed("summary"))
                self.assertFalse(server._save_main_chat_kv_if_needed("fact_extract"))
                self.assertFalse(server._save_main_chat_kv_if_needed("child_extract"))
                self.assertFalse(server._save_main_chat_kv_if_needed("stage1_search"))
                self.assertEqual(mock_post.call_count, 1)
                self.assertIn("/slots/0?action=save", mock_post.call_args[0][0])

                # 次のメイン会話推論直前に1回だけ restore される
                mock_post.reset_mock()
                self.assertTrue(server._restore_main_chat_kv_if_needed("main_chat"))
                self.assertFalse(server._restore_main_chat_kv_if_needed("main_chat_second"))
                self.assertEqual(mock_post.call_count, 1)
                self.assertIn("/slots/0?action=restore", mock_post.call_args[0][0])
        finally:
            server._PROFILE_BAT_PATH = orig_profile_path


    def test_13_worldline_filter_toggle(self):
        """有向木世界線フィルタ(worldline_filter_enabled)のON/OFFで記憶の棲み分けが切り替わることを検証。"""
        sid = "test_worldline_filter_session"
        # ブランチAの記憶を登録
        r_a = self.client.post(
            f"/memory/add?session_id={sid}&message_id=msg_branch_a",
            json={"texts": ["ブランチAの特有記憶データ"]},
            headers=self.headers,
        )
        self.assertEqual(r_a.status_code, 200)

        # ブランチBの記憶を登録
        r_b = self.client.post(
            f"/memory/add?session_id={sid}&message_id=msg_branch_b",
            json={"texts": ["ブランチBの特有記憶データ"]},
            headers=self.headers,
        )
        self.assertEqual(r_b.status_code, 200)

        # 1. worldline_filter_enabled=true (既定) かつ active_message_ids=msg_branch_a の場合 -> ブランチAのみ取得される
        res_filtered = self.client.get(
            f"/memory/list?session_id={sid}&active_message_ids=msg_branch_a&worldline_filter_enabled=true",
            headers=self.headers,
        )
        self.assertEqual(res_filtered.status_code, 200)
        data_filtered = res_filtered.get_json()
        self.assertEqual(len(data_filtered["memories"]), 1)
        self.assertIn("ブランチA", data_filtered["memories"][0]["text"])

        # 2. worldline_filter_enabled=false の場合 -> active_message_ids が指定されていても世界線を越えて全記憶(2件)が返る
        res_unfiltered = self.client.get(
            f"/memory/list?session_id={sid}&active_message_ids=msg_branch_a&worldline_filter_enabled=false",
            headers=self.headers,
        )
        self.assertEqual(res_unfiltered.status_code, 200)
        data_unfiltered = res_unfiltered.get_json()
        self.assertEqual(len(data_unfiltered["memories"]), 2)

    def test_14_precache_endpoint_and_kv_slot_handover(self):
        """非同期KVキャッシュ先読み(is_precache / force_clear_saved_kv)時のRAG非注入・KVスロット引き継ぎ・プロンプト末尾正規化を検証。"""
        from unittest.mock import MagicMock, patch

        # 1. _strip_trailing_generation_prompt の検証
        raw_prompt = "<|turn>user\nこんにちは<turn|>\n<|turn>model\n<|channel>thought\n<channel|>応答テスト<turn|>\n<|turn>model\n<|channel>thought\n<channel|>"
        msgs_assistant_end = [
            {"role": "user", "content": "こんにちは"},
            {"role": "assistant", "content": "応答テスト"},
        ]
        stripped = server._strip_trailing_generation_prompt(raw_prompt, msgs_assistant_end)
        self.assertEqual(
            stripped,
            "<|turn>user\nこんにちは<turn|>\n<|turn>model\n<|channel>thought\n<channel|>応答テスト<turn|>\n",
        )
        msgs_user_end = [{"role": "user", "content": "こんにちは"}]
        unstripped = server._strip_trailing_generation_prompt(raw_prompt, msgs_user_end)
        self.assertEqual(unstripped, raw_prompt)

        # 2. is_precache + force_clear_saved_kv リクエスト時の挙動検証
        sid = "test_precache_session"
        self.client.post(
            f"/memory/add?session_id={sid}&message_id=msg_p1",
            json={"texts": ["先行プリキャッシュ時はRAG注入されない確認用記憶"]},
            headers=self.headers,
        )

        # 要約完了直後を模擬して _main_chat_kv_saved = True をセット
        server._main_chat_kv_saved = True
        server._main_chat_has_kv = False

        captured_payloads = []

        def fake_post(url, json=None, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.headers = {"Content-Type": "application/json"}
            if url.endswith("/v1/chat/completions"):
                captured_payloads.append(dict(json or {}))
                mock_resp.content = b'{"choices":[],"timings":{"cache_n":120,"prompt_n":45}}'
                mock_resp.json.return_value = {"choices": [], "timings": {"cache_n": 120, "prompt_n": 45}}
                return mock_resp
            if url.endswith("/apply-template"):
                mock_resp.json.return_value = {"prompt": stripped}
                return mock_resp
            if url.endswith("/tokenize"):
                mock_resp.json.return_value = {"tokens": [1, 2, 3]}
                return mock_resp
            raise AssertionError(f"Unexpected URL called during precache: {url}")

        with patch.object(server._http_session, "post", side_effect=fake_post):
            res = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemma-4",
                    "messages": [
                        {"role": "system", "content": "システムプロンプト"},
                        {"role": "user", "content": "先行プリキャッシュ時はRAG注入されない確認用記憶について"},
                        {"role": "assistant", "content": "確定したAI応答"},
                    ],
                    "n_predict": 0,
                    "stream": False,
                    "cache_prompt": True,
                    "id_slot": 0,
                    "add_generation_prompt": False,
                    "is_precache": True,
                    "force_clear_saved_kv": True,
                    "session_id": sid,
                },
                headers=self.headers,
            )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(captured_payloads), 1)
        forwarded = captured_payloads[0]

        # 内部制御用フィールドは llama-server へ転送されないこと
        self.assertNotIn("is_precache", forwarded)
        self.assertNotIn("force_clear_saved_kv", forwarded)
        self.assertEqual(forwarded.get("add_generation_prompt"), False)
        self.assertEqual(forwarded.get("n_predict"), 0)
        self.assertEqual(forwarded.get("id_slot"), 0)

        # is_precache 時は動的RAG（[コンテキスト情報]）が注入されず、末尾に空の user プレースホルダが付与されること
        forwarded_msgs = forwarded.get("messages", [])
        for m in forwarded_msgs:
            self.assertNotIn("[コンテキスト情報]", m.get("content", ""))
        self.assertEqual(len(forwarded_msgs), 4)
        self.assertEqual(forwarded_msgs[-1], {"role": "user", "content": ""})

        # _strip_trailing_generation_prompt が空の user プレースホルダ末尾でも <|turn>user\n まで正確に揃えること
        placeholder_prompt = "<|turn>user\nこんにちは<turn|>\n<|turn>model\n応答テスト<turn|>\n<|turn>user\n<turn|>\n<|turn>model\n<|channel>thought\n<channel|>"
        stripped_placeholder = server._strip_trailing_generation_prompt(placeholder_prompt, forwarded_msgs)
        self.assertEqual(
            stripped_placeholder,
            "<|turn>user\nこんにちは<turn|>\n<|turn>model\n応答テスト<turn|>\n<|turn>user\n",
        )

        # force_clear_saved_kv により古い退避フラグが破棄され、スロット0がアクティブ状態に更新されること
        self.assertFalse(server._main_chat_kv_saved)
        self.assertTrue(server._main_chat_has_kv)


if __name__ == "__main__":
    unittest.main()
