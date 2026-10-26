import os
import re
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CHAT_STORE_PATH = os.path.join(
    REPO_ROOT,
    "backend",
    "llama.cpp-b9873",
    "tools",
    "ui",
    "src",
    "lib",
    "stores",
    "chat.svelte.ts",
)
CONV_STORE_PATH = os.path.join(
    REPO_ROOT,
    "backend",
    "llama.cpp-b9873",
    "tools",
    "ui",
    "src",
    "lib",
    "stores",
    "conversations.svelte.ts",
)


def reconcile_summary_snapshot_ref(
    snapshot_index: int,
    snapshot_list: list,
    display_count: int,
    history_text_snapshot: str = "",
    is_sliding_snapshot=None,
    max_summary_blocks: int = 16,
):
    """conversations.svelte.ts の reconcileSummarySnapshot と同一ロジックのリファレンス実装。"""
    if display_count <= 0 or len(snapshot_list) == 0:
        return {
            "adjustedList": [],
            "clampedIndex": 0,
            "isSliding": False,
            "historyText": "",
        }

    if snapshot_index <= 0:
        repaired_index = max(0, display_count - 20)
        return {
            "adjustedList": list(snapshot_list),
            "clampedIndex": repaired_index,
            "isSliding": (
                is_sliding_snapshot
                if is_sliding_snapshot is not None
                else (len(snapshot_list) >= max_summary_blocks)
            ),
            "historyText": history_text_snapshot or (snapshot_list[-1] if snapshot_list else ""),
        }

    if snapshot_index <= display_count:
        return {
            "adjustedList": list(snapshot_list),
            "clampedIndex": snapshot_index,
            "isSliding": (
                is_sliding_snapshot
                if is_sliding_snapshot is not None
                else (len(snapshot_list) >= max_summary_blocks)
            ),
            "historyText": history_text_snapshot or "",
        }

    avg_block_size = max(10, round(snapshot_index / max(1, len(snapshot_list))))
    estimated_blocks = min(len(snapshot_list), display_count // avg_block_size)
    adjusted_list = list(snapshot_list[:estimated_blocks])
    clamped_index = min(snapshot_index, display_count) if len(adjusted_list) > 0 else 0
    is_sliding = (
        (
            is_sliding_snapshot
            if is_sliding_snapshot is not None
            else (len(adjusted_list) >= max_summary_blocks)
        )
        if len(adjusted_list) > 0
        else False
    )
    history_text = (history_text_snapshot or "") if len(adjusted_list) > 0 else ""
    return {
        "adjustedList": adjusted_list,
        "clampedIndex": clamped_index,
        "isSliding": is_sliding,
        "historyText": history_text,
    }


class TestUiStores(unittest.TestCase):
    def setUp(self):
        with open(CHAT_STORE_PATH, "r", encoding="utf-8") as f:
            self.chat_src = f.read()
        with open(CONV_STORE_PATH, "r", encoding="utf-8") as f:
            self.conv_src = f.read()

    def test_no_undefined_display_messages_in_chat_store(self):
        """chat.svelte.ts 内に未定義の displayMessages 参照が残っていないことを確認。"""
        matches = re.findall(r"\bdisplayMessages\b", self.chat_src)
        self.assertEqual(matches, [], f"Undefined displayMessages found in chat.svelte.ts: {matches}")

    def test_no_nonexistent_get_active_messages_method(self):
        """chat.svelte.ts 内に存在しない this.getActiveMessages 呼び出しが残っていないことを確認。"""
        self.assertNotIn("this.getActiveMessages", self.chat_src)

    def test_rebuild_memory_trailing_snapshot_no_out_of_scope_summary_text(self):
        """rebuildAllMemories のループ外 trailingMessages 更新でループ内変数 summaryText を参照しないことを確認。"""
        trailing_block_match = re.search(
            r"const trailingMessages = targetMessages\.slice\(finalEndIdx\);[\s\S]{0,400}?updateMessage\([\s\S]{0,300}?\);",
            self.chat_src,
        )
        self.assertIsNotNone(trailing_block_match, "trailingMessages block not found in rebuildAllMemories")
        block_text = trailing_block_match.group(0)
        self.assertNotIn("aiHistoryTextSnapshot: summaryText", block_text)
        self.assertIn("aiHistoryTextSnapshot: lastSummaryText", block_text)

    def test_reconcile_summary_snapshot_exported_and_used(self):
        """reconcileSummarySnapshot が conversations.svelte.ts から export され、chat.svelte.ts で使用されていることを確認。"""
        self.assertIn("export function reconcileSummarySnapshot(", self.conv_src)
        self.assertIn("reconcileSummarySnapshot", self.chat_src)
        call_matches = re.findall(r"\breconcileSummarySnapshot\s*\(", self.chat_src)
        self.assertGreaterEqual(len(call_matches), 2, "reconcileSummarySnapshot should be called in chat.svelte.ts")

    def test_reconcile_summary_snapshot_invariants(self):
        """要約スナップショット整合関数の不変条件を検証。"""
        # 1. 空リスト時は index が正でも 0 にリセットされる
        r1 = reconcile_summary_snapshot_ref(20, [], 30, "old")
        self.assertEqual(r1["clampedIndex"], 0)
        self.assertEqual(r1["adjustedList"], [])
        self.assertEqual(r1["historyText"], "")

        # 2. 要約リストが存在し index <= 0 の場合は自己修復される
        r2 = reconcile_summary_snapshot_ref(0, ["block1", "block2"], 45, "")
        self.assertEqual(r2["clampedIndex"], 25)
        self.assertEqual(r2["adjustedList"], ["block1", "block2"])
        self.assertEqual(r2["historyText"], "block2")

        # 3. 削除により displayCount が減少した場合、0 <= clampedIndex <= displayCount を満たす
        r3 = reconcile_summary_snapshot_ref(40, ["b1", "b2"], 25, "b2")
        self.assertTrue(0 <= r3["clampedIndex"] <= 25)
        self.assertEqual(len(r3["adjustedList"]), 1)

        # 4. 削除によりブロックが0件になった場合、clampedIndex も 0 になる
        r4 = reconcile_summary_snapshot_ref(20, ["b1"], 5, "b1")
        self.assertEqual(r4["adjustedList"], [])
        self.assertEqual(r4["clampedIndex"], 0)
        self.assertFalse(r4["isSliding"])

    def test_summary_system_prompt_forbids_participant_names_and_reused_keywords(self):
        """DEFAULT_SUMMARY_SYSTEM_PROMPT に当事者名・大カッコ使用禁止・既出キーワード禁止・相対時間表現禁止ルールが含まれることを確認。"""
        self.assertIn("NEVER highlight participant names", self.chat_src)
        self.assertIn("Do NOT Highlight Following Keywords", self.chat_src)
        self.assertIn("episode-specific keywords", self.chat_src)
        self.assertIn("Strictly NO square brackets like [ ]", self.chat_src)
        self.assertIn("NATURALLY WITHIN the summary sentences", self.chat_src)
        self.assertIn("relative time expressions", self.chat_src)
        self.assertIn("that day", self.chat_src)

    def test_summary_prompt_builder_injects_forbidden_recent_keywords(self):
        """buildEpisodeSummaryUserPrompt と extractRecentKeywordsFromSummaryList が export され、直近キーワードが注入され二重括弧がサニタイズされることを検証。"""
        self.assertIn("export function extractRecentKeywordsFromSummaryList(", self.chat_src)
        self.assertIn("export function buildEpisodeSummaryUserPrompt(", self.chat_src)

        # Python側リファレンス実装による動作検証（大カッコ二重囲みサニタイズを含む）
        def extract_ref(summary_list, max_blocks=4):
            recent = summary_list[-max_blocks:]
            bracket_re = re.compile(r"""(?:[「『｢【［\[“"‘'])(.*?)(?:[」』｣】］\]”"’'])""")
            keys = []
            for b in recent:
                normalized = re.sub(r"「\s*\[(.*?)\]\s*」", r"「\1」", b)
                clean = re.sub(r"^\[エピソード\s*\d+\]:\s*", "", normalized).strip()
                for m in bracket_re.finditer(clean):
                    raw = m.group(1).strip()
                    w = re.sub(r"^[\[\(\{\"\'‘“]+|[\]\)\}\"\'’”]+$", "", raw).strip()
                    if w and len(w) < 50 and "\n" not in w and w not in keys:
                        keys.append(w)
            return keys

        def build_prompt_ref(ai_history, log_text, max_blocks=4):
            episode_lines = []
            for i, s in enumerate(ai_history):
                cleaned_s = re.sub(r"「\s*\[(.*?)\]\s*」", r"「\1」", s)
                episode_lines.append(f"[Episode {i+1}]: {cleaned_s}")
            prev = (
                "\n## Previous Context (Summary Background)\n"
                + "\n\n".join(episode_lines)
                + "\n"
                if ai_history
                else "\nNo previous summary available."
            )
            keys = extract_ref(ai_history, max_blocks)
            forbidden = (
                "\n## Do NOT Highlight Following Keywords (Already Used in Recent Episodes):\n"
                + "\n".join(f"- 「{k}」" for k in keys)
                + "\n"
                if keys
                else ""
            )
            return f"{prev}{forbidden}\n## New Conversation Log\n{log_text}\n## Output (Provide only the summary text matching the conversation language):"

        # 過去要約なしの場合: forbidden セクション無し
        p_empty = build_prompt_ref([], "User: こんにちは")
        self.assertNotIn("Do NOT Highlight Following Keywords", p_empty)
        self.assertIn("No previous summary available.", p_empty)

        # 過去要約に二重括弧（「[夕暮れ時]」）が含まれる場合でもクリーンな単語としてサニタイズ抽出される
        past = [
            "ユーザーと「[夕暮れ時]」を眺めたエピソード。",
            "喫茶店で「[腕に絡む]」様子を描写したエピソード。",
        ]
        p_with_keys = build_prompt_ref(past, "User: 次はどこへ行く？")
        self.assertIn("Do NOT Highlight Following Keywords", p_with_keys)
        self.assertIn("- 「夕暮れ時」", p_with_keys)
        self.assertIn("- 「腕に絡む」", p_with_keys)
        self.assertNotIn("[夕暮れ時]", p_with_keys)
        self.assertNotIn("[腕に絡む]", p_with_keys)

        # summarizeRecentConversation と rebuildAllMemories の双方で buildEpisodeSummaryUserPrompt が使われていること
        matches = re.findall(r"\bbuildEpisodeSummaryUserPrompt\s*\(", self.chat_src)
        self.assertGreaterEqual(len(matches), 2)

    def test_forgotten_blocks_archived_sequentially_with_await(self):
        """16ブロック到達時の先頭2ブロック押し出しで archiveForgottenBlockToRag が必ず await で直列実行されることを確認。"""
        # chat.svelte.ts 内の this.archiveForgottenBlockToRag 呼び出しがすべて await 付きであること
        unawaited_chat = re.findall(r"(?<!await\s)this\.archiveForgottenBlockToRag\s*\(", self.chat_src)
        self.assertEqual(unawaited_chat, [], f"Unawaited archiveForgottenBlockToRag in chat.svelte.ts: {unawaited_chat}")
        awaited_chat = re.findall(r"await\s+this\.archiveForgottenBlockToRag\s*\(", self.chat_src)
        self.assertGreaterEqual(len(awaited_chat), 2)

        # conversations.svelte.ts 内の chatStore.archiveForgottenBlockToRag 呼び出しも await 付きであること
        unawaited_conv = re.findall(r"(?<!await\s)chatStore\.archiveForgottenBlockToRag\s*\(", self.conv_src)
        self.assertEqual(unawaited_conv, [], f"Unawaited archiveForgottenBlockToRag in conversations.svelte.ts: {unawaited_conv}")
        awaited_conv = re.findall(r"await\s+chatStore\.archiveForgottenBlockToRag\s*\(", self.conv_src)
        self.assertGreaterEqual(len(awaited_conv), 1)

    def test_tts_emoji_stripping_across_pipeline(self):
        """TTSパイプライン（UIセグメント・avatar-core・TTSサーバー）で絵文字が除去され、表情解析には元テキストが渡ることを確認。"""
        assistant_path = os.path.join(
            REPO_ROOT,
            "backend",
            "llama.cpp-b9873",
            "tools",
            "ui",
            "src",
            "lib",
            "components",
            "app",
            "chat",
            "ChatMessages",
            "ChatMessage",
            "ChatMessageAssistant",
            "ChatMessageAssistant.svelte",
        )
        avatar_core_path = os.path.join(
            REPO_ROOT,
            "backend",
            "llama.cpp-b9873",
            "tools",
            "ui",
            "static",
            "deining",
            "avatar-core-v2.js",
        )
        tts_server_path = os.path.join(
            REPO_ROOT, "backend", "Irodori-TTS", "tts_server.py"
        )
        with open(assistant_path, "r", encoding="utf-8") as f:
            assistant_svelte = f.read()
        with open(avatar_core_path, "r", encoding="utf-8") as f:
            avatar_core = f.read()
        with open(tts_server_path, "r", encoding="utf-8") as f:
            tts_server_src = f.read()

        self.assertIn("Extended_Pictographic", assistant_svelte)
        self.assertIn("playTTS(seg.speakableText, true, seg.rawText)", assistant_svelte)
        self.assertIn("Extended_Pictographic", avatar_core)
        self.assertIn("analyzeExpressionTimeline(emotionText)", avatar_core)
        self.assertIn(r"\U00010000-\U0010ffff", tts_server_src)


if __name__ == "__main__":
    unittest.main()
