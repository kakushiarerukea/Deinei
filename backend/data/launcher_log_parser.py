"""
泥濘 (Deinei) 統合ランチャー用 ログパーサー & プロファイル管理 (純粋関数モジュール)
--------------------------------------------------------------------------------
server.py や GUI (tkinter) に依存せず、各サーバープロセスの標準出力行から
リアルタイム指標 (TPS, KVキャッシュ再利用率, Web検索ワード, RAGコサイン類似度等) の抽出、
ノイズログのフィルタ判定、および deining_profile.bat の読み書きを行う純粋関数群。
"""

import re
from typing import Any, Dict, List, Optional

# ANSI エスケープシーケンス除去用
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

# LLM (llama-server.exe) パース用正規表現
_LLM_PROMPT_EVAL_RE = re.compile(
    r"prompt eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens\s*\(\s*([\d.]+)\s*ms per token,\s*([\d.]+)\s*tokens per second\)"
)
_LLM_EVAL_RE = re.compile(
    r"(?<!prompt )eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens\s*\(\s*([\d.]+)\s*ms per token,\s*([\d.]+)\s*tokens per second\)"
)
_LLM_RELEASE_TOKENS_RE = re.compile(
    r"stop processing:\s*n_tokens\s*=\s*(\d+),\s*truncated\s*=\s*(\d+)"
)
_LLM_CTX_SLOT_RE = re.compile(r"n_ctx_slot\s*=\s*(\d+)")

# Flask (server.py) パース用正規表現
_FLASK_CACHE_INIT_RE = re.compile(r"\[Token Cache Debug\]\s*初回ターン\s*総トークン数:\s*(\d+)")
_FLASK_CACHE_DIFF_RE = re.compile(
    r"前回:\s*(\d+)\s*tok\s*\|\s*今回:\s*(\d+)\s*tok\s*\|\s*共通プレフィックス（キャッシュ再利用）:\s*(\d+)\s*tok\s*\(([\d.]+)%\)"
)
_FLASK_CACHE_MISMATCH_RE = re.compile(r"食い違い位置:\s*(\d+)\s*トークン目")
_FLASK_SEARCH_TOGGLE_RE = re.compile(r"Web検索:\s*(ON|OFF)")
_FLASK_SEARCH_QUERY_RE = re.compile(r"検索:\s*'(.+)'")
_FLASK_SEARCH_EXEC_RE = re.compile(r'検索実行:\s*"(.+?)"\s*→\s*(\d+)\s*件取得')

# RAG 記憶検索デバッグ & コサイン類似度パース用正規表現
_RAG_QUERY_HEADER_RE = re.compile(r"クエリ:\s*「(.*?)」\s*\(コレクション総数:\s*(\d+)件\)")
_RAG_FACT_SECTION_RE = re.compile(r"Fact\s*記憶\s*検索結果\s*\((\d+)件候補")
_RAG_EPISODE_SECTION_RE = re.compile(r"親子リトリーバル\s*\(Episode\)\s*検索結果\s*\((\d+)件候補\)")
_RAG_WEB_SECTION_RE = re.compile(r"即席Webリトリーバル.*検索結果\s*\((\d+)件候補\)")
_RAG_TOP6_SECTION_RE = re.compile(r"統合RAG想起ランキング\s*\(Top\s*6\s*厳選\)")

_RAG_FACT_ITEM_RE = re.compile(
    r"^\s*\[(.+?)\]\s*類似度:\s*([\d.]+)\s*\(距離:\s*([\d.]+)\)\s*\|\s*内容:\s*(.+)$"
)
_RAG_KEY_PARENT_ITEM_RE = re.compile(
    r"^\s*\[(.+?)\]\s*類似度:\s*([\d.]+)\s*\(距離:\s*([\d.]+)\)\s*\|\s*キー:\s*「(.*?)」\s*->\s*親:\s*(.+)$"
)
_RAG_TOP6_ITEM_RE = re.compile(
    r"^\s*#(\d+)\s*\[([A-Za-z_]+)\s*\]\s*類似度:\s*([\d.]+)\s*\|\s*(.+)$"
)
_RAG_FINAL_COUNTS_RE = re.compile(
    r"最終想起エピソード件数:\s*(\d+)件\s*/\s*Fact件数:\s*(\d+)件"
)

# TTS (tts_server.py) パース用正規表現
_TTS_REQ_RE = re.compile(r"\[TTS Server\]\s*Request received:\s*split_chunks=(True|False),\s*speed=([\d.]+)")
_TTS_RAW_TEXT_RE = re.compile(r"テキスト（生,\s*(\d+)文字）:\s*(.+)")
_TTS_CLEAN_TEXT_RE = re.compile(r"クリーニング後\s*\((\d+)文字\):\s*(.+)")
_TTS_DONE_RE = re.compile(r'"POST\s+/tts\s+HTTP/1\.[01]"\s+200\b')

# Gateway (deinei_net.py) パース用正規表現
_GW_URL_RE = re.compile(r"^\s*>>\s*(https?://\S+)")
_GW_ACCESS_RE = re.compile(r'\[INFO\]\s*Gateway:\s*([\d.a-fA-F:]+)\s*-\s*"(\w+)\s+([^\s"]+)\s+HTTP/1\.[01]"\s+(\d+)')

# ノイズ判定用の静的ファイル・ポーリングパス
_NOISE_HTTP_PATHS = (
    "/health",
    "/props",
    "/v1/models",
    "/v1/streams/lookup",
    "/tools",
    "/_app/version.json",
    "/build.json",
    "/favicon",
    "/api/search/status",
    "/api/avatar/models",
    "/api/search/toggle",
)
_NOISE_STATIC_EXTS = (
    ".js",
    ".css",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".model3.json",
    ".exp3.json",
    ".motion3.json",
    ".physics3.json",
    ".pose3.json",
    ".cdi3.json",
    ".moc3",
    ".wav",
)


def strip_ansi(text: str) -> str:
    """文字列から ANSI エスケープシーケンスを除去する。"""
    if not text:
        return ""
    return _ANSI_ESCAPE_RE.sub("", text)


def clean_status_label(raw_status: str) -> Dict[str, Any]:
    """RAG検索の採否文字列から装飾を除き、構造化された採否情報を返す。"""
    text = (raw_status or "").strip().strip("[]").strip()
    is_rejected = "不採用" in text
    adopted = ("採用" in text) and not is_rejected
    if adopted:
        if "候補" in text:
            return {"adopted": True, "label": "[OK] 採用候補", "reason": "採用候補"}
        return {"adopted": True, "label": "[OK] 採用", "reason": "採用"}
    if "世界線外" in text:
        return {"adopted": False, "label": "[--] 世界線外", "reason": "世界線外"}
    if "期限切れ" in text:
        return {"adopted": False, "label": "[--] 期限切れ", "reason": "期限切れ"}
    if "重複" in text:
        return {"adopted": False, "label": "[--] 重複除外", "reason": "重複親"}
    if "類似度不足" in text:
        return {"adopted": False, "label": "[--] 類似度不足", "reason": "類似度不足"}
    return {"adopted": False, "label": "[--] 除外", "reason": text}


def is_noise_log_line(source: str, line: str) -> bool:
    """コンソール表示においてノイズとなる定常ポーリング・静的配信ログかを判定する。"""
    clean = strip_ansi(line).strip()
    if not clean:
        return True

    # 共通の HTTP アクセスログ判定 (GET / OPTIONS 等のポーリングや静的ファイル)
    if '"GET ' in clean or '"OPTIONS ' in clean or '"HEAD ' in clean:
        for p in _NOISE_HTTP_PATHS:
            if f" {p}" in clean or f"{p}?" in clean or f'"{p}' in clean:
                return True
        # クエリ文字列を除いたパス部分で静的拡張子を検査
        m_path = re.search(r'"(?:GET|OPTIONS|HEAD)\s+([^\s"?]+)', clean)
        if m_path:
            req_path = m_path.group(1).lower()
            if any(req_path.endswith(ext) for ext in _NOISE_STATIC_EXTS):
                # 200 / 304 / 404 は通常の静的ファイル要求としてノイズ扱い
                if any(code in clean for code in (" 200 ", " 304 ", " 404 ")):
                    return True

    src = (source or "").lower()
    if src == "llm":
        # llama-server の冗長なスロット内部状態ログ
        if "slot get_availabl:" in clean or "slot launch_slot_:" in clean:
            return True
        if "total time =" in clean or "graphs reused =" in clean:
            return True
    elif src == "tts":
        # Irodori-TTS の定常的なサンプリングレート注意やモデル再確認ログ
        if "Using the default SDR of" in clean:
            return True
        if "Reducing the sampling rate of the original audio" in clean:
            return True
        if clean.startswith("ローカルモデルを読み込みます:"):
            return True
        if set(clean) <= {"=", "-"} and len(clean) >= 10:
            return True

    return False


def parse_llm_log_line(line: str) -> Dict[str, Any]:
    """llama-server.exe の1行ログから推論速度 (TPS) やトークン数、ロード状態を抽出する。"""
    clean = strip_ansi(line).strip()
    result: Dict[str, Any] = {}
    if not clean:
        return result

    if "llama_server: model loaded" in clean or "main: model loaded" in clean:
        result["model_loaded"] = True

    m_ctx = _LLM_CTX_SLOT_RE.search(clean)
    if m_ctx:
        result["n_ctx_slot"] = int(m_ctx.group(1))

    m_pe = _LLM_PROMPT_EVAL_RE.search(clean)
    if m_pe:
        result["prompt_eval_ms"] = float(m_pe.group(1))
        result["prompt_eval_tokens"] = int(m_pe.group(2))
        result["prompt_ms_per_token"] = float(m_pe.group(3))
        result["prompt_tps"] = float(m_pe.group(4))

    m_ev = _LLM_EVAL_RE.search(clean)
    if m_ev:
        result["eval_ms"] = float(m_ev.group(1))
        result["eval_tokens"] = int(m_ev.group(2))
        result["eval_ms_per_token"] = float(m_ev.group(3))
        result["eval_tps"] = float(m_ev.group(4))

    m_rel = _LLM_RELEASE_TOKENS_RE.search(clean)
    if m_rel:
        result["n_tokens"] = int(m_rel.group(1))
        result["truncated"] = int(m_rel.group(2))

    return result


def parse_flask_log_line(line: str, current_rag_section: str = "fact") -> Dict[str, Any]:
    """server.py (Flask) の1行ログから KVキャッシュ指標、Web検索ワード、RAG類似度情報を抽出する。"""
    clean = strip_ansi(line).rstrip()
    result: Dict[str, Any] = {}
    if not clean.strip():
        return result

    # 1. Token Cache Debug
    m_init = _FLASK_CACHE_INIT_RE.search(clean)
    if m_init:
        tok = int(m_init.group(1))
        result["token_cache"] = {
            "is_initial": True,
            "prev_tokens": 0,
            "curr_tokens": tok,
            "reused_tokens": 0,
            "reuse_pct": 0.0,
            "mismatch_pos": None,
        }
        return result

    m_diff = _FLASK_CACHE_DIFF_RE.search(clean)
    if m_diff:
        result["token_cache"] = {
            "is_initial": False,
            "prev_tokens": int(m_diff.group(1)),
            "curr_tokens": int(m_diff.group(2)),
            "reused_tokens": int(m_diff.group(3)),
            "reuse_pct": float(m_diff.group(4)),
            "mismatch_pos": None,
        }
        return result

    m_mis = _FLASK_CACHE_MISMATCH_RE.search(clean)
    if m_mis:
        result["token_cache_mismatch_pos"] = int(m_mis.group(1))
        return result

    # 2. Web検索トグル & 検索クエリ
    m_toggle = _FLASK_SEARCH_TOGGLE_RE.search(clean)
    if m_toggle:
        result["search_enabled"] = (m_toggle.group(1) == "ON")
        return result

    m_sq = _FLASK_SEARCH_QUERY_RE.search(clean)
    if m_sq:
        result["search_query"] = m_sq.group(1).strip()
        return result

    m_sexec = _FLASK_SEARCH_EXEC_RE.search(clean)
    if m_sexec:
        result["search_query"] = m_sexec.group(1).strip()
        result["search_result_count"] = int(m_sexec.group(2))
        return result

    # 3. RAG 記憶検索デバッグ セクション開始・ヘッダー
    m_qhead = _RAG_QUERY_HEADER_RE.search(clean)
    if m_qhead:
        result["rag_query_header"] = {
            "query": m_qhead.group(1),
            "collection_count": int(m_qhead.group(2)),
        }
        result["rag_section"] = "fact"
        return result

    if _RAG_FACT_SECTION_RE.search(clean):
        result["rag_section"] = "fact"
        return result

    if _RAG_EPISODE_SECTION_RE.search(clean):
        result["rag_section"] = "episode"
        return result

    if _RAG_WEB_SECTION_RE.search(clean):
        result["rag_section"] = "web"
        return result

    if _RAG_TOP6_SECTION_RE.search(clean):
        result["rag_section"] = "top6"
        return result

    # 4. RAG 個別候補行 (Fact / Episode親子 / 即席Web)
    m_fact = _RAG_FACT_ITEM_RE.match(clean)
    if m_fact:
        st = clean_status_label(m_fact.group(1))
        result["rag_candidate"] = {
            "category": "FACT",
            "adopted": st["adopted"],
            "status_label": st["label"],
            "reason": st["reason"],
            "similarity": float(m_fact.group(2)),
            "distance": float(m_fact.group(3)),
            "key": "-",
            "content": m_fact.group(4).strip(),
        }
        return result

    m_kp = _RAG_KEY_PARENT_ITEM_RE.match(clean)
    if m_kp:
        st = clean_status_label(m_kp.group(1))
        cat = "WEB" if current_rag_section == "web" else "EPISODE"
        result["rag_candidate"] = {
            "category": cat,
            "adopted": st["adopted"],
            "status_label": st["label"],
            "reason": st["reason"],
            "similarity": float(m_kp.group(2)),
            "distance": float(m_kp.group(3)),
            "key": m_kp.group(4).strip(),
            "content": m_kp.group(5).strip(),
        }
        return result

    m_top6 = _RAG_TOP6_ITEM_RE.match(clean)
    if m_top6:
        result["rag_top6_item"] = {
            "rank": int(m_top6.group(1)),
            "category": m_top6.group(2).strip().upper(),
            "similarity": float(m_top6.group(3)),
            "preview": m_top6.group(4).strip(),
        }
        return result

    m_counts = _RAG_FINAL_COUNTS_RE.search(clean)
    if m_counts:
        result["rag_final_counts"] = {
            "episode_count": int(m_counts.group(1)),
            "fact_count": int(m_counts.group(2)),
        }
        return result

    return result


def parse_tts_log_line(line: str) -> Dict[str, Any]:
    """tts_server.py の1行ログから合成リクエスト状態・読み上げセリフを抽出する。"""
    clean = strip_ansi(line).strip()
    result: Dict[str, Any] = {}
    if not clean:
        return result

    m_req = _TTS_REQ_RE.search(clean)
    if m_req:
        result["tts_status"] = "SYNTHESIZING"
        result["split_chunks"] = (m_req.group(1) == "True")
        result["speed"] = float(m_req.group(2))
        return result

    m_raw = _TTS_RAW_TEXT_RE.search(clean)
    if m_raw:
        result["raw_char_count"] = int(m_raw.group(1))
        result["raw_text"] = m_raw.group(2).strip()
        return result

    m_clean = _TTS_CLEAN_TEXT_RE.search(clean)
    if m_clean:
        result["clean_char_count"] = int(m_clean.group(1))
        result["clean_text"] = m_clean.group(2).strip()
        result["tts_status"] = "SYNTHESIZING"
        return result

    if _TTS_DONE_RE.search(clean):
        result["tts_status"] = "ONLINE"
        result["tts_completed"] = True
        return result

    return result


def parse_gateway_log_line(line: str) -> Dict[str, Any]:
    """deinei_net.py (Gateway) の1行ログから公開URLやクライアントアクセスを抽出する。"""
    clean = strip_ansi(line).strip()
    result: Dict[str, Any] = {}
    if not clean:
        return result

    m_url = _GW_URL_RE.match(clean)
    if m_url:
        result["gateway_url"] = m_url.group(1).strip()
        return result

    m_acc = _GW_ACCESS_RE.search(clean)
    if m_acc:
        result["gateway_access"] = {
            "client_ip": m_acc.group(1),
            "method": m_acc.group(2),
            "path": m_acc.group(3),
            "status": int(m_acc.group(4)),
        }
        return result

    return result


def parse_profile_bat(content: str) -> Dict[str, Any]:
    """deining_profile.bat のテキスト内容から設定辞書を抽出する。"""
    profile: Dict[str, Any] = {
        "PROFILE_NAME": "16GB (High)",
        "GPU_NAME": "Unknown GPU",
        "VRAM_GB": "16.0",
        "NCMOE": 9,
        "MMPROJ_ARGS": "",
        "ALLOW_LAN": 0,
    }
    if not content:
        return profile

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("::") or line.lower().startswith("@echo"):
            continue
        m_quoted = re.match(r'^set\s+"([A-Za-z0-9_]+)=(.*)"\s*$', line, re.IGNORECASE)
        if m_quoted:
            key = m_quoted.group(1).upper()
            val = m_quoted.group(2).strip()
        else:
            m_plain = re.match(r"^set\s+([A-Za-z0-9_]+)=(.*)$", line, re.IGNORECASE)
            if not m_plain:
                continue
            key = m_plain.group(1).upper()
            val = m_plain.group(2).strip()

        if key == "NCMOE":
            try:
                profile["NCMOE"] = int(val)
            except ValueError:
                pass
        elif key == "ALLOW_LAN":
            try:
                profile["ALLOW_LAN"] = int(val)
            except ValueError:
                pass
        elif key in profile:
            profile[key] = val

    return profile


def serialize_profile_bat(profile: Dict[str, Any]) -> str:
    """設定辞書から deining_profile.bat のバッチファイル内容 (CRLF) を生成する。"""
    p_name = str(profile.get("PROFILE_NAME", "16GB (High)"))
    gpu_name = str(profile.get("GPU_NAME", "Unknown GPU"))
    vram_gb = str(profile.get("VRAM_GB", "16.0"))
    try:
        ncmoe = int(profile.get("NCMOE", 9))
    except (ValueError, TypeError):
        ncmoe = 9
    mmproj = str(profile.get("MMPROJ_ARGS", "")).strip()
    try:
        allow_lan = int(profile.get("ALLOW_LAN", 0))
    except (ValueError, TypeError):
        allow_lan = 0

    lines: List[str] = [
        "@echo off",
        ":: ======================================================================",
        ":: Deinei Auto-Tuning Configuration File",
        f":: Detected Hardware: {gpu_name} (VRAM: approx {vram_gb}GB)",
        f":: Applied Profile: {p_name}",
        ":: ======================================================================",
        "",
        f"set PROFILE_NAME={p_name}",
        f"set GPU_NAME={gpu_name}",
        f"set VRAM_GB={vram_gb}",
        "",
        ":: MoE CPU Offload Experts (8GB/10GB:20, 12GB:16, 16GB:9, 24GB:0)",
        f"set NCMOE={ncmoe}",
        "",
        ":: Vision Encoder settings",
        f"set MMPROJ_ARGS={mmproj}",
        "",
        ":: LAN Access (0: Local only, 1: Allow same Wi-Fi/LAN devices e.g. smartphones)",
        f"set ALLOW_LAN={allow_lan}",
        "",
    ]
    return "\r\n".join(lines)
