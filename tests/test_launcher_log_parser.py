import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", "data")))

from launcher_log_parser import (
    clean_status_label,
    is_noise_log_line,
    parse_flask_log_line,
    parse_gateway_log_line,
    parse_llm_log_line,
    parse_profile_bat,
    parse_tts_log_line,
    serialize_profile_bat,
    strip_ansi,
)


class TestLauncherLogParser(unittest.TestCase):
    def test_strip_ansi(self):
        self.assertEqual(strip_ansi("\x1b[32mINFO\x1b[0m: test"), "INFO: test")

    def test_llm_log_parsing(self):
        pe_line = (
            "1.01.910.542 I slot print_timing: id  0 | task 0 | "
            "prompt eval time =     950.27 ms /  1521 tokens (    0.62 ms per token,  1600.59 tokens per second)"
        )
        res_pe = parse_llm_log_line(pe_line)
        self.assertAlmostEqual(res_pe["prompt_eval_ms"], 950.27)
        self.assertEqual(res_pe["prompt_eval_tokens"], 1521)
        self.assertAlmostEqual(res_pe["prompt_tps"], 1600.59)

        ev_line = (
            "1.01.910.549 I slot print_timing: id  0 | task 0 | "
            "       eval time =    3477.43 ms /   245 tokens (   14.19 ms per token,    70.45 tokens per second)"
        )
        res_ev = parse_llm_log_line(ev_line)
        self.assertAlmostEqual(res_ev["eval_ms"], 3477.43)
        self.assertEqual(res_ev["eval_tokens"], 245)
        self.assertAlmostEqual(res_ev["eval_tps"], 70.45)

        rel_line = "1.01.911.034 I slot      release: id  0 | task 0 | stop processing: n_tokens = 1765, truncated = 0"
        res_rel = parse_llm_log_line(rel_line)
        self.assertEqual(res_rel["n_tokens"], 1765)
        self.assertEqual(res_rel["truncated"], 0)

        load_line = "0.13.774.168 I srv  llama_server: model loaded"
        self.assertTrue(parse_llm_log_line(load_line).get("model_loaded"))

    def test_flask_token_cache_and_search_parsing(self):
        init_line = "[Token Cache Debug] 初回ターン 総トークン数: 1518"
        res_init = parse_flask_log_line(init_line)
        self.assertTrue(res_init["token_cache"]["is_initial"])
        self.assertEqual(res_init["token_cache"]["curr_tokens"], 1518)

        diff_line = "前回: 1518 tok | 今回: 1779 tok | 共通プレフィックス（キャッシュ再利用）: 1468 tok (96.7%)"
        res_diff = parse_flask_log_line(diff_line)
        self.assertFalse(res_diff["token_cache"]["is_initial"])
        self.assertEqual(res_diff["token_cache"]["prev_tokens"], 1518)
        self.assertEqual(res_diff["token_cache"]["curr_tokens"], 1779)
        self.assertEqual(res_diff["token_cache"]["reused_tokens"], 1468)
        self.assertAlmostEqual(res_diff["token_cache"]["reuse_pct"], 96.7)

        mis_line = "  食い違い位置: 1468 トークン目"
        self.assertEqual(parse_flask_log_line(mis_line)["token_cache_mismatch_pos"], 1468)

        self.assertTrue(parse_flask_log_line("Web検索: ON")["search_enabled"])
        self.assertFalse(parse_flask_log_line("Web検索: OFF")["search_enabled"])

        sq_line = "検索: '板橋区 明日 天気'"
        self.assertEqual(parse_flask_log_line(sq_line)["search_query"], "板橋区 明日 天気")

        sexec_line = '検索実行: "高市早苗 政策" → 3 件取得'
        res_sexec = parse_flask_log_line(sexec_line)
        self.assertEqual(res_sexec["search_query"], "高市早苗 政策")
        self.assertEqual(res_sexec["search_result_count"], 3)

    def test_flask_rag_cosine_similarity_parsing(self):
        q_line = "クエリ: 「おはよう、昨日の水族館楽しかったね」 (コレクション総数: 42件)"
        res_q = parse_flask_log_line(q_line)
        self.assertEqual(res_q["rag_query_header"]["query"], "おはよう、昨日の水族館楽しかったね")
        self.assertEqual(res_q["rag_query_header"]["collection_count"], 42)

        fact_ok = "  [採用] 類似度: 0.8840 (距離: 0.1160) | 内容: - [3日前] ユーザーはブラックコーヒーが好き"
        res_f1 = parse_flask_log_line(fact_ok, current_rag_section="fact")
        cand1 = res_f1["rag_candidate"]
        self.assertEqual(cand1["category"], "FACT")
        self.assertTrue(cand1["adopted"])
        self.assertAlmostEqual(cand1["similarity"], 0.8840)
        self.assertAlmostEqual(cand1["distance"], 0.1160)
        self.assertIn("ブラックコーヒー", cand1["content"])

        fact_ng = "  [[不採用: 類似度不足 (類似度: 0.7912 < 0.865)] 類似度: 0.7912 (距離: 0.2088) | 内容: 誕生日は11月"
        res_f2 = parse_flask_log_line(fact_ng, current_rag_section="fact")
        self.assertFalse(res_f2["rag_candidate"]["adopted"])
        self.assertEqual(res_f2["rag_candidate"]["status_label"], "[--] 類似度不足")
        self.assertEqual(res_f2["rag_candidate"]["reason"], "類似度不足")

        fact_wl_ng = "  [[不採用: 世界線外] 類似度: 0.9200 (距離: 0.0800) | 内容: 別世界線の記憶"
        res_f3 = parse_flask_log_line(fact_wl_ng, current_rag_section="fact")
        self.assertFalse(res_f3["rag_candidate"]["adopted"])
        self.assertEqual(res_f3["rag_candidate"]["status_label"], "[--] 世界線外")
        self.assertEqual(res_f3["rag_candidate"]["reason"], "世界線外")

        ep_sec = "--- 親子リトリーバル (Episode) 検索結果 (3件候補) ---"
        self.assertEqual(parse_flask_log_line(ep_sec)["rag_section"], "episode")

        ep_ok = "  [[採用]] 類似度: 0.9120 (距離: 0.0880) | キー: 「水族館」 -> 親: 先週二人で池袋の水族館へ出かけた..."
        res_ep = parse_flask_log_line(ep_ok, current_rag_section="episode")
        cand_ep = res_ep["rag_candidate"]
        self.assertEqual(cand_ep["category"], "EPISODE")
        self.assertTrue(cand_ep["adopted"])
        self.assertEqual(cand_ep["key"], "水族館")
        self.assertAlmostEqual(cand_ep["similarity"], 0.9120)
        self.assertIn("池袋の水族館", cand_ep["content"])

        ep_wl_ng = "  [[不採用: 世界線外 (msg_999)] 類似度: 0.9150 (距離: 0.0850) | キー: 「海」 -> 親: 先週海に行った..."
        res_ep_wl = parse_flask_log_line(ep_wl_ng, current_rag_section="episode")
        self.assertFalse(res_ep_wl["rag_candidate"]["adopted"])
        self.assertEqual(res_ep_wl["rag_candidate"]["status_label"], "[--] 世界線外")
        self.assertEqual(res_ep_wl["rag_candidate"]["reason"], "世界線外")

        ep_dup_ng = "  [[不採用: 重複親エピソード]] 類似度: 0.9010 (距離: 0.0990) | キー: 「イルカ」 -> 親: 先週二人で池袋の水族館へ出かけた..."
        res_ep_dup = parse_flask_log_line(ep_dup_ng, current_rag_section="episode")
        self.assertFalse(res_ep_dup["rag_candidate"]["adopted"])
        self.assertEqual(res_ep_dup["rag_candidate"]["status_label"], "[--] 重複除外")
        self.assertEqual(res_ep_dup["rag_candidate"]["reason"], "重複親")

        web_sec = "--- 即席Webリトリーバル (5時間有効 / session=default) 検索結果 (2件候補) ---"
        self.assertEqual(parse_flask_log_line(web_sec)["rag_section"], "web")

        web_ok = "  [[採用候補]] 類似度: 0.8750 (距離: 0.1250) | キー: 「板橋区 天気」 -> 親: 明日は晴れ 最高気温22度..."
        res_web = parse_flask_log_line(web_ok, current_rag_section="web")
        cand_web = res_web["rag_candidate"]
        self.assertEqual(cand_web["category"], "WEB")
        self.assertTrue(cand_web["adopted"])
        self.assertEqual(cand_web["key"], "板橋区 天気")

        web_exp_ng = "  [[不採用: 期限切れ (> 5時間)] 類似度: 0.8900 (距離: 0.1100) | キー: 「板橋区 天気」 -> 親: 昨日の天気..."
        res_web_exp = parse_flask_log_line(web_exp_ng, current_rag_section="web")
        self.assertFalse(res_web_exp["rag_candidate"]["adopted"])
        self.assertEqual(res_web_exp["rag_candidate"]["status_label"], "[--] 期限切れ")
        self.assertEqual(res_web_exp["rag_candidate"]["reason"], "期限切れ")

        top6_line = "  #1 [EPISODE] 類似度: 0.9120 | [当時のエピソード 1]: 先週二人で池袋の水族館へ..."
        res_top6 = parse_flask_log_line(top6_line, current_rag_section="top6")
        self.assertEqual(res_top6["rag_top6_item"]["rank"], 1)
        self.assertEqual(res_top6["rag_top6_item"]["category"], "EPISODE")
        self.assertAlmostEqual(res_top6["rag_top6_item"]["similarity"], 0.9120)

    def test_tts_and_gateway_parsing(self):
        tts_clean = "クリーニング後 (56文字): ふふっ、君の手、すごくあったかくて気持ちいい…。"
        res_tts = parse_tts_log_line(tts_clean)
        self.assertEqual(res_tts["clean_char_count"], 56)
        self.assertIn("すごくあったかくて", res_tts["clean_text"])
        self.assertEqual(res_tts["tts_status"], "SYNTHESIZING")

        tts_done = 'INFO:     192.168.11.4:0 - "POST /tts HTTP/1.1" 200 OK'
        self.assertTrue(parse_tts_log_line(tts_done)["tts_completed"])

        gw_url = "  >> http://192.168.11.4:8090/?token=abc123xyz"
        self.assertEqual(parse_gateway_log_line(gw_url)["gateway_url"], "http://192.168.11.4:8090/?token=abc123xyz")

    def test_noise_filter(self):
        self.assertTrue(is_noise_log_line("flask", '127.0.0.1 - - [16/Oct/2026] "GET /health HTTP/1.1" 200 -'))
        self.assertTrue(is_noise_log_line("flask", '127.0.0.1 - - [18/Oct/2026 01:48:49] "GET /api/search/status HTTP/1.1" 200 -'))
        self.assertTrue(is_noise_log_line("flask", '127.0.0.1 - - [18/Oct/2026 01:48:51] "GET /api/avatar/models HTTP/1.1" 200 -'))
        self.assertTrue(is_noise_log_line("flask", '127.0.0.1 - - [18/Oct/2026 01:48:52] "OPTIONS /api/search/toggle HTTP/1.1" 200 -'))
        self.assertTrue(is_noise_log_line("gw", '[INFO] Gateway: 192.168.11.4 - "GET /deining/ulvm2_0001/animation/idle.exp3.json HTTP/1.1" 304 -'))
        self.assertTrue(is_noise_log_line("gw", '[INFO] Gateway: 192.168.11.4 - "GET /props?autoload=false HTTP/1.1" 200 -'))
        self.assertTrue(is_noise_log_line("tts", "Using the default SDR of 47 dB"))
        self.assertFalse(is_noise_log_line("gw", '[INFO] Gateway: 192.168.11.4 - "POST /__deinei/api/v1/chat/completions HTTP/1.1" 200 -'))
        self.assertFalse(is_noise_log_line("flask", "前回: 1518 tok | 今回: 1779 tok | 共通プレフィックス（キャッシュ再利用）: 1468 tok (96.7%)"))

    def test_profile_bat_roundtrip(self):
        sample = (
            "@echo off\r\n"
            "set PROFILE_NAME=16GB (High)\r\n"
            "set GPU_NAME=NVIDIA GeForce RTX 5070 Ti\r\n"
            "set VRAM_GB=15.9\r\n"
            "set NCMOE=9\r\n"
            "set MMPROJ_ARGS=--mmproj mmproj-BF16.gguf\r\n"
            "set ALLOW_LAN=1\r\n"
        )
        parsed = parse_profile_bat(sample)
        self.assertEqual(parsed["PROFILE_NAME"], "16GB (High)")
        self.assertEqual(parsed["NCMOE"], 9)
        self.assertEqual(parsed["MMPROJ_ARGS"], "--mmproj mmproj-BF16.gguf")
        self.assertEqual(parsed["ALLOW_LAN"], 1)

        serialized = serialize_profile_bat(parsed)
        reparsed = parse_profile_bat(serialized)
        self.assertEqual(reparsed, parsed)

        # 値の一部に引用符が含まれる場合でも末尾引用符が欠損しないこと
        quoted_sample = (
            "@echo off\r\n"
            'set "PROFILE_NAME=24GB (Max)"\r\n'
            'set MMPROJ_ARGS=--mmproj "mmproj-BF16.gguf"\r\n'
        )
        q_parsed = parse_profile_bat(quoted_sample)
        self.assertEqual(q_parsed["PROFILE_NAME"], "24GB (Max)")
        self.assertEqual(q_parsed["MMPROJ_ARGS"], '--mmproj "mmproj-BF16.gguf"')

        # 不正な数値型が渡されても serialize_profile_bat が例外を出さずフォールバックすること
        fallback_bat = serialize_profile_bat({"NCMOE": "not_an_int", "ALLOW_LAN": None})
        fb_parsed = parse_profile_bat(fallback_bat)
        self.assertEqual(fb_parsed["NCMOE"], 9)
        self.assertEqual(fb_parsed["ALLOW_LAN"], 0)

    def test_gui_launcher_telemetry_integration(self):
        from deinei_launcher import DeineiLauncherGUI, parse_args

        args = parse_args(["--no-autostart", "--no-browser", "--mode", "lan"])
        app = DeineiLauncherGUI(args)
        app.is_shutting_down = True
        try:
            app._process_single_log(
                "llm",
                "1.01.910.542 I slot print_timing: id  0 | task 0 | prompt eval time =     950.27 ms /  1521 tokens (    0.62 ms per token,  1600.59 tokens per second)",
            )
            app._process_single_log(
                "llm",
                "1.01.910.549 I slot print_timing: id  0 | task 0 |        eval time =    3477.43 ms /   245 tokens (   14.19 ms per token,    70.45 tokens per second)",
            )
            app._process_single_log(
                "llm",
                "1.01.911.034 I slot      release: id  0 | task 0 | stop processing: n_tokens = 1765, truncated = 0",
            )
            app._process_single_log(
                "flask",
                "前回: 1518 tok | 今回: 1779 tok | 共通プレフィックス（キャッシュ再利用）: 1468 tok (96.7%)",
            )
            app._process_single_log("flask", "  食い違い位置: 1468 トークン目")
            app._process_single_log("flask", "検索: '板橋区 明日 天気'")
            app._process_single_log("flask", "クエリ: 「おはよう」 (コレクション総数: 42件)")
            app._process_single_log("flask", "--- Fact 記憶 検索結果 (2件候補 / 閾値: 類似度 >= 0.865) ---")
            app._process_single_log(
                "flask",
                "  [採用] 類似度: 0.8840 (距離: 0.1160) | 内容: - [3日前] ユーザーはブラックコーヒーが好き",
            )
            app._process_single_log("flask", "--- 親子リトリーバル (Episode) 検索結果 (1件候補) ---")
            app._process_single_log(
                "flask",
                "  [採用] 類似度: 0.9120 (距離: 0.0880) | キー: 「水族館」 -> 親: 先週二人で池袋の水族館へ出かけた...",
            )
            app._process_single_log("flask", "最終想起エピソード件数: 1件 / Fact件数: 1件")
            app._process_single_log("flask", "==================== 統合RAG想起ランキング (Top 6 厳選) ====================")
            app._process_single_log(
                "flask",
                "  #1 [EPISODE] 類似度: 0.9120 | [当時のエピソード 1]: 先週二人で池袋の水族館へ出かけた...",
            )
            app._process_single_log("tts", "クリーニング後 (56文字): ふふっ、君の手、すごくあったかくて気持ちいい…。")
            app._process_single_log("gw", "  >> http://192.168.11.4:8090/?token=testtoken123")

            self.assertIn("70.45 t/s", app.metric_tps_var.get())
            self.assertIn("1,468 tok 再利用 (96.7%)", app.metric_kv_reuse_var.get())
            self.assertIn("1,468 トークン目", app.metric_kv_mismatch_var.get())
            self.assertIn("板橋区 明日 天気", app.metric_search_query_var.get())
            self.assertIn("すごくあったかくて", app.metric_tts_text_var.get())
            self.assertEqual(app.gateway_url_var.get(), "http://192.168.11.4:8090/?token=testtoken123")

            children = app.rag_tree.get_children()
            self.assertEqual(len(children), 2)
            ep_vals = app.rag_tree.item(children[1], "values")
            self.assertEqual(ep_vals[0], "[OK] 採用 #1")
            self.assertEqual(ep_vals[4], "水族館")
        finally:
            app.root.destroy()

    def test_pywebview_bridge_and_html_template(self):
        from deinei_launcher import LauncherApiBridge, parse_args

        html_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "backend", "data", "launcher_ui.html")
        )
        self.assertTrue(os.path.isfile(html_path))
        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        # 要件確認: スプラッシュ画面表示ボタンとWeb検索ON/OFF切替ボタンが存在しないこと
        self.assertNotIn("open_splash_in_browser", html_content)
        self.assertNotIn("toggle_web_search_api", html_content)
        self.assertNotIn("ON/OFF 切替", html_content)
        self.assertIn("ragTableBody", html_content)

        args = parse_args(["--no-autostart", "--no-browser", "--mode", "lan"])
        bridge = LauncherApiBridge(args)
        bridge.is_shutting_down = True
        try:
            init_state = bridge.get_initial_state()
            self.assertEqual(init_state["gateway_mode"], "lan")

            bridge._process_single_log(
                "llm",
                "1.01.910.549 I slot print_timing: id  0 | task 0 |        eval time =    3477.43 ms /   245 tokens (   14.19 ms per token,    70.45 tokens per second)",
            )
            bridge._process_single_log(
                "llm",
                "1.01.911.034 I slot      release: id  0 | task 0 | stop processing: n_tokens = 1765, truncated = 0",
            )
            bridge._process_single_log(
                "flask",
                "前回: 1518 tok | 今回: 1779 tok | 共通プレフィックス（キャッシュ再利用）: 1468 tok (96.7%)",
            )
            bridge._process_single_log("flask", "クエリ: 「テスト発話」 (コレクション総数: 15件)")
            bridge._process_single_log("flask", "--- 親子リトリーバル (Episode) 検索結果 (2件候補) ---")
            bridge._process_single_log(
                "flask",
                "  [[不採用: 世界線外 (msg_other)] 類似度: 0.9120 (距離: 0.0880) | キー: 「別ブランチ」 -> 親: 別世界線の記憶...",
            )
            bridge._process_single_log(
                "flask",
                "  [[採用]] 類似度: 0.9120 (距離: 0.0880) | キー: 「水族館」 -> 親: 先週二人で池袋の水族館へ出かけた...",
            )
            bridge._process_single_log("flask", "==================== 統合RAG想起ランキング (Top 6 厳選) ====================")
            bridge._process_single_log(
                "flask",
                "  #1 [EPISODE] 類似度: 0.9120 | [当時のエピソード 1]: 先週二人で池袋の水族館へ出かけた...",
            )
            bridge._process_single_log("gw", "  >> http://192.168.11.4:8090/?token=testtoken123")

            snap = bridge.poll_updates(0, -1)
            self.assertGreater(snap["last_seq"], 0)
            self.assertIn("70.45 t/s", snap["telemetry"]["tps"])
            self.assertAlmostEqual(snap["telemetry"]["kv_pct"], 96.7)
            self.assertTrue(snap["rag_updated"])
            self.assertEqual(len(snap["rag_rows"]), 2)
            self.assertEqual(snap["rag_rows"][0]["status"], "[--] 世界線外")
            self.assertEqual(snap["rag_rows"][0]["tag"], "worldline_out")
            self.assertFalse(snap["rag_rows"][0]["adopted"])
            self.assertEqual(snap["rag_rows"][1]["status"], "[OK] 採用 #1")
            self.assertEqual(snap["rag_rows"][1]["tag"], "adopted_top")

            qr_res = bridge.get_qr_svg()
            self.assertTrue(qr_res["ok"])
            self.assertIn("<svg", qr_res["svg"])

            bridge.clear_rag_history()
            snap2 = bridge.poll_updates(snap["last_seq"], snap["rag_version"])
            self.assertTrue(snap2["rag_updated"])
            self.assertEqual(len(snap2["rag_rows"]), 0)
        finally:
            bridge.shutdown()

    def test_stop_popen_process_guards_exited_pid(self):
        from unittest.mock import MagicMock, patch
        import deinei_launcher

        exited_proc = MagicMock()
        exited_proc.pid = 99999
        exited_proc.poll.return_value = 0

        running_proc = MagicMock()
        running_proc.pid = 88888
        running_proc.poll.return_value = None

        with patch.object(deinei_launcher, "kill_process_tree") as mock_kill:
            deinei_launcher.stop_popen_process(exited_proc)
            mock_kill.assert_not_called()

            deinei_launcher.stop_popen_process(running_proc)
            mock_kill.assert_called_once_with(88888)


if __name__ == "__main__":
    unittest.main()


