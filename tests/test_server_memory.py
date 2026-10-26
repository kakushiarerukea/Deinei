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


if __name__ == "__main__":
    unittest.main()
