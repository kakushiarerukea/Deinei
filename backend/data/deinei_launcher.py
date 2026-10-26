"""
泥濘 (Deinei) 統合デスクトップGUIランチャー (deinei_launcher.py)
--------------------------------------------------------------------------------
4つのサーバープロセス:
  1. LLM Server (llama-server.exe : 8080)
  2. Data / RAG Server (server.py : 5000)
  3. TTS Server (tts_server.py : 5001)
  4. Auth Gateway (deinei_net.py : 8090, LAN / Tailscale モード時)
を単一のウィンドウで一括管理し、リアルタイム指標 (TPS, KVキャッシュ再利用率,
Web検索キーワード, RAG記憶・親子リトリーバルのコサイン類似度) とログを集約表示する。
"""

import argparse
from datetime import datetime
import glob
import http.client
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from typing import Any, Dict, List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_CURRENT_DIR)
_ROOT_DIR = os.path.dirname(_BACKEND_DIR)
_CACHE_DIR = os.path.join(_ROOT_DIR, ".cache")

os.environ.setdefault("UV_CACHE_DIR", os.path.join(_CACHE_DIR, "uv"))
os.environ.setdefault("UV_PYTHON_INSTALL_DIR", os.path.join(_CACHE_DIR, "uv_python"))
os.environ.setdefault("PIP_CACHE_DIR", os.path.join(_CACHE_DIR, "pip"))
os.environ.setdefault("HF_HOME", os.path.join(_CACHE_DIR, "huggingface"))
os.environ.setdefault("TORCH_HOME", os.path.join(_CACHE_DIR, "torch"))

if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

from launcher_log_parser import (
    is_noise_log_line,
    parse_flask_log_line,
    parse_gateway_log_line,
    parse_llm_log_line,
    parse_profile_bat,
    parse_tts_log_line,
    serialize_profile_bat,
    strip_ansi,
)
from net_discovery import (
    download_and_install_tailscale,
    get_lan_ip_candidates,
    get_network_profiles,
    get_tailscale_info,
    trigger_tailscale_login,
)

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _normalize_to_python_exe(py_path: str) -> str:
    """pythonw.exe が渡された場合、同一ディレクトリの python.exe に正規化する。"""
    if not py_path:
        return "python"
    base = os.path.basename(py_path).lower()
    if base == "pythonw.exe":
        cand = os.path.join(os.path.dirname(py_path), "python.exe")
        if os.path.isfile(cand):
            return cand
    return py_path


def _is_healthy_backend_python(py_exe: str) -> bool:
    """指定された Python 環境で flask と chromadb がインポート可能か検証する。"""
    if not py_exe:
        return False
    # 現在の実行プロセスと同一環境であればインプロセスで即座に判定
    cur_exe = _normalize_to_python_exe(sys.executable or "")
    if os.path.isfile(py_exe) and os.path.isfile(cur_exe):
        try:
            if os.path.samefile(py_exe, cur_exe):
                import chromadb  # noqa: F401
                import flask  # noqa: F401

                return True
        except Exception:
            return False

    try:
        res = subprocess.run(
            [py_exe, "-c", "import flask, chromadb"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
            timeout=5,
        )
        return res.returncode == 0
    except Exception:
        return False


def resolve_python_executable(root_dir: str) -> str:
    """使用する Python 実行ファイルパスをパッケージ健全性チェック付きで特定する。"""
    candidates: List[str] = []

    # 1. 統合仮想環境 (backend\.venv)
    venv_py = os.path.join(root_dir, "backend", ".venv", "Scripts", "python.exe")
    if os.path.isfile(venv_py):
        candidates.append(venv_py)

    # 2. 現在実行中の Python (pythonw.exe -> python.exe 正規化)
    if sys.executable:
        cur_py = _normalize_to_python_exe(sys.executable)
        if os.path.isfile(cur_py) and cur_py not in candidates:
            candidates.append(cur_py)

    # 3. Windows 標準の Python 3.10 インストール先および PATH 上の python
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        py310 = os.path.join(local_app_data, "Programs", "Python", "Python310", "python.exe")
        if os.path.isfile(py310) and py310 not in candidates:
            candidates.append(py310)

    which_py = shutil.which("python")
    if which_py and os.path.isfile(which_py) and which_py not in candidates:
        candidates.append(which_py)

    # 4. 旧 TTS 仮想環境 (backend\Irodori-TTS\.venv)
    tts_venv_py = os.path.join(root_dir, "backend", "Irodori-TTS", ".venv", "Scripts", "python.exe")
    if os.path.isfile(tts_venv_py) and tts_venv_py not in candidates:
        candidates.append(tts_venv_py)

    # 健全性チェック (import flask, chromadb) を満たす最初の環境を採用
    for cand in candidates:
        if _is_healthy_backend_python(cand):
            return cand

    # フォールバック
    return candidates[0] if candidates else (_normalize_to_python_exe(sys.executable) or "python")



def check_http_health(host: str, port: int, path: str = "/health", timeout: float = 0.8) -> Tuple[bool, str]:
    """HTTP ヘルスチェックを実行する。"""
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="replace")
        status = resp.status
        conn.close()
        if status == 200:
            if body:
                try:
                    j = json.loads(body)
                    st = j.get("status")
                    if st == "error":
                        return (False, "ERROR (MODEL)")
                    if st and st != "ok":
                        return (False, f"LOADING ({st})")
                except Exception:
                    pass
            return (True, "ONLINE")
        elif status == 503:
            return (False, "LOADING (503)")
        elif status >= 500:
            return (False, f"ERROR (HTTP {status})")
        return (False, f"HTTP {status}")
    except Exception:
        return (False, "OFFLINE")


def check_port_listening(host: str, port: int, timeout: float = 0.5) -> bool:
    """指定ポートが TCP 接続を受け付けているか確認する。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def kill_process_tree(pid: int) -> None:
    """指定 PID とその子孫プロセスツリーを終了する。"""
    if not pid or pid <= 4:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
                timeout=5,
            )
        except Exception:
            pass
    else:
        try:
            subprocess.run(
                ["pkill", "-TERM", "-P", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
        except Exception:
            pass
        try:
            os.kill(pid, 15)
        except Exception:
            pass


def stop_popen_process(proc: Optional[subprocess.Popen]) -> None:
    """生存中の Popen プロセスのみツリーごと安全に終了する (終了済みPID再利用による誤爆防止)。"""
    if proc is None:
        return
    try:
        if proc.poll() is None:
            kill_process_tree(proc.pid)
    except Exception:
        pass


def cleanup_orphan_servers(target_ports: Optional[List[int]] = None) -> List[str]:
    """指定ポート (既定: 8080, 5000, 5001, 8090) に残存している Deinei 関連プロセスのみを終了する。"""
    messages: List[str] = []
    if os.name != "nt":
        return messages

    ports = target_ports if target_ports is not None else [8080, 5000, 5001, 8090]
    if not ports:
        return messages

    ports_str = ", ".join(str(int(p)) for p in ports)
    ps_script = (
        f"$ports = @({ports_str}); "
        "foreach ($p in $ports) { "
        "  $conns = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue; "
        "  foreach ($c in $conns) { "
        "    $pidToKill = $c.OwningProcess; "
        "    if ($pidToKill -and $pidToKill -gt 4 -and $pidToKill -ne $PID) { "
        "      $proc = Get-Process -Id $pidToKill -ErrorAction SilentlyContinue; "
        "      if ($proc -and ($proc.ProcessName -match 'python|llama-server')) { "
        "        $wmi = Get-CimInstance Win32_Process -Filter \"ProcessId = $pidToKill\" -ErrorAction SilentlyContinue; "
        "        $cmdLine = if ($wmi -and $wmi.CommandLine) { $wmi.CommandLine } else { '' }; "
        "        $isDeinei = ($proc.ProcessName -match 'llama-server') -or ($cmdLine -match 'server\\.py|tts_server\\.py|deinei_net\\.py|llama-server'); "
        "        if ($isDeinei) { "
        "          cmd.exe /c \"taskkill /F /T /PID $pidToKill\" 2>&1 | Out-Null; "
        "          if ($LASTEXITCODE -eq 0) { "
        "            Write-Output ('Killed port ' + $p + ' PID ' + $pidToKill + ' (' + $proc.ProcessName + ')'); "
        "          } "
        "        } "
        "      } "
        "    } "
        "  } "
        "}"
    )
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            timeout=8,
        )
        for line in (res.stdout or "").splitlines():
            if line.strip():
                messages.append(line.strip())
    except Exception:
        pass
    return messages


def ensure_jinja_template(root_dir: str) -> None:
    """sandbox 内に gemma_chat_template.jinja が未配置の場合に自動補完する。"""
    sandbox_dir = os.path.join(root_dir, "llama-b9873-bin-win-cuda-13.3-x64sandbox")
    target = os.path.join(sandbox_dir, "gemma_chat_template.jinja")
    if os.path.isfile(target):
        return
    os.makedirs(sandbox_dir, exist_ok=True)
    candidates = [
        os.path.join(root_dir, "backend", "templates", "gemma_chat_template.jinja"),
        os.path.join(root_dir, "backend", "llama.cpp-b9873", "models", "templates", "google-gemma-4-31B-it.jinja"),
    ]
    for src in candidates:
        if os.path.isfile(src):
            try:
                shutil.copyfile(src, target)
            except Exception:
                pass
            break


def find_llm_model(root_dir: str) -> str:
    """sandbox または親フォルダから推論対象の GGUF モデルパスを検索する。"""
    sandbox_dir = os.path.join(root_dir, "llama-b9873-bin-win-cuda-13.3-x64sandbox")
    patterns = [
        os.path.join(sandbox_dir, "*.gguf"),
        os.path.join(os.path.dirname(root_dir), "*.gguf"),
    ]
    for pat in patterns:
        for path in sorted(glob.glob(pat)):
            if "mmproj" not in os.path.basename(path).lower():
                return path
    return os.path.join(sandbox_dir, "Goetia-26B-A4B-v1.7.i1-IQ4_XS.gguf")


def find_mmproj_model(root_dir: str) -> str:
    """sandbox 内から Vision エンコーダ (mmproj) ファイル名を検索する。"""
    sandbox_dir = os.path.join(root_dir, "llama-b9873-bin-win-cuda-13.3-x64sandbox")
    for path in sorted(glob.glob(os.path.join(sandbox_dir, "*mmproj*.gguf"))):
        return os.path.basename(path)
    return "mmproj-BF16.gguf"


def build_doctor_report_text() -> str:
    """ネットワーク・サーバー接続診断 (Doctor) レポート文字列を生成する。"""
    lines: List[str] = []
    lines.append("========================================================")
    lines.append(" 泥濘 (Deinei) ネットワーク・接続診断 (Doctor)")
    lines.append("========================================================")
    lines.append("")

    lines.append("[1/4] バックエンド AI サーバー稼働状態:")
    backends = [
        ("LLM Server (WebUI & 推論)", "127.0.0.1", 8080, "/health"),
        ("Data Server (記憶/検索/会話)", "127.0.0.1", 5000, "/health"),
        ("TTS Server (音声合成)", "127.0.0.1", 5001, "/health"),
        ("Auth Gateway (外部端末中継)", "127.0.0.1", 8090, "/__deinei/health"),
    ]
    for name, host, port, hpath in backends:
        is_open = check_port_listening(host, port)
        if not is_open:
            status_tag = "[INFO]" if port == 8090 else "[WARN]"
            lines.append(f"  - {status_tag} {name} (Port {port}): 停止中")
        else:
            ok, msg = check_http_health(host, port, hpath)
            mark = "[OK]  " if ok else "[WARN]"
            lines.append(f"  - {mark} {name} (Port {port}): 稼働中 ({msg})")

    lines.append("")
    lines.append("[2/4] LAN IPv4 アドレス候補 (仮想アダプタ・リンクローカル除外済み):")
    candidates = get_lan_ip_candidates()
    if candidates:
        for idx, c in enumerate(candidates):
            rec = " (推奨)" if idx == 0 else ""
            lines.append(f"  - [OK] {c['interface']}: {c['ip']}{rec}")
    else:
        lines.append("  - [WARN] 有効なプライベート LAN IPv4 が検出されませんでした。")

    lines.append("")
    lines.append("[3/4] Windows ネットワークプロファイル診断:")
    profiles = get_network_profiles()
    public_found = False
    if profiles:
        for p in profiles:
            cat = p.get("category", "Unknown")
            is_pub = (cat.lower() == "public")
            mark = "[WARN]" if is_pub else "[OK]  "
            lines.append(f"  - {mark} 接続名: '{p.get('name')}' ({p.get('interface')}) -> カテゴリ: {cat}")
            if is_pub:
                public_found = True
        if public_found:
            lines.append("    [注意] ネットワークが 'Public' (パブリック) に設定されていると、")
            lines.append("    Windows ファイアウォールにより同一Wi-Fiのスマホからの着信が遮断されることがあります。")
            lines.append("    自宅Wi-Fiの場合はネットワーク設定を 'プライベート' に変更することを推奨します。")
    else:
        lines.append("  - [INFO] ネットワークプロファイル情報を取得できませんでした。")

    lines.append("")
    lines.append("[4/4] Tailscale 連携状態:")
    ts = get_tailscale_info()
    if not ts.get("installed"):
        lines.append("  - [INFO] Tailscale CLI は未検出です (未インストール)。")
    else:
        lines.append(f"  - [OK] Tailscale CLI 検出: {ts.get('binary_path')}")
        if ts.get("running"):
            lines.append(f"  - [OK] Tailscale 状態: 稼働中 (IPv4: {ts.get('ip')})")
            if ts.get("dns_name"):
                lines.append(f"  - [OK] MagicDNS 名: {ts.get('dns_name')}")
        else:
            lines.append(f"  - [WARN] Tailscale は未接続です ({ts.get('error')})")

    lines.append("")
    lines.append("========================================================")
    return "\n".join(lines)


# ==============================================================================
# Tkinter デスクトップ GUI ランチャー本体
# ==============================================================================
class DeineiLauncherGUI:
    def __init__(self, args: argparse.Namespace):
        import tkinter as tk
        from tkinter import messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.args = args
        self.root_dir = _ROOT_DIR
        self.profile_path = os.path.join(self.root_dir, "deining_profile.bat")
        self.py_exe = resolve_python_executable(self.root_dir)

        # プロセス管理辞書
        self.procs: Dict[str, Optional[subprocess.Popen]] = {
            "llm": None,
            "flask": None,
            "tts": None,
            "gw": None,
        }
        self.proc_lock = threading.Lock()
        self.log_queue: "queue.Queue[Tuple[str, str]]" = queue.Queue()
        self.is_shutting_down = False

        # RAG パース状態管理
        self.current_rag_section = "fact"
        self.rag_candidates_buffer: List[Dict[str, Any]] = []
        self.rag_top6_ranks: Dict[str, int] = {}

        # プロファイル読み込み
        self.profile = self._load_profile()

        # 初期 Gateway モードの決定
        env_gw = os.environ.get("DEINEI_GATEWAY_MODE", "").strip().lower()
        init_mode = (args.mode or env_gw or "local").lower()
        if init_mode not in ("local", "lan", "tailscale"):
            init_mode = "local"
        self._gateway_mode = init_mode
        self._tailscale_https = bool(args.https)

        # Tkinter ルートウィンドウ構築
        self.root = tk.Tk()
        self.root.title("泥濘 (Deinei) 統合サーバーランチャー & テレメトリコンソール")
        self.root.geometry("1180x820")
        self.root.minsize(980, 680)
        self.root.configure(bg="#0f1420")

        # アイコン設定 (存在する場合)
        icon_path = os.path.join(
            self.root_dir, "backend", "llama.cpp-b9873", "tools", "ui", "static", "favicon.ico"
        )
        if os.path.isfile(icon_path):
            try:
                self.root.iconbitmap(icon_path)
            except Exception:
                pass

        # 状態変数 (Tkinter Variables)
        self.gateway_mode_var = tk.StringVar(value=init_mode)
        self.tailscale_https_var = tk.BooleanVar(value=bool(args.https))
        self.gateway_url_var = tk.StringVar(value="ローカル専用モード (127.0.0.1:8080)")
        self.filter_noise_var = tk.BooleanVar(value=True)
        self.autoscroll_var = tk.BooleanVar(value=True)

        # リアルタイムメトリクス変数
        self.metric_tps_var = tk.StringVar(value="--.-- t/s")
        self.metric_prompt_eval_var = tk.StringVar(value="Prompt: -- ms (-- tok)")
        self.metric_ctx_var = tk.StringVar(value="0 / 16,384 tok (0.0%)")
        self.metric_kv_reuse_var = tk.StringVar(value="待機中 (初回発話後に計測)")
        self.metric_kv_mismatch_var = tk.StringVar(value="食い違い位置: -")

        self.metric_search_toggle_var = tk.StringVar(value="ON")
        self.metric_search_query_var = tk.StringVar(value="(未実行)")
        self.metric_tts_state_var = tk.StringVar(value="待機中")
        self.metric_tts_text_var = tk.StringVar(value="(まだ読み上げられたセリフはありません)")

        self.rag_summary_var = tk.StringVar(
            value="クエリ: (未実行) | コレクション総数: - | 採用: Episode 0件 / Fact 0件"
        )
        self.rag_selected_detail_var = tk.StringVar(
            value="テーブルの行をクリックすると、ここに検索キーと記憶・親エピソードの内容が表示されます。"
        )
        self.profile_summary_var = tk.StringVar(value=self._format_profile_summary())

        self._setup_styles()
        self._build_ui()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close_window)

        # 定期更新タイマー & バックグラウンドヘルスチェック開始
        self.root.after(100, self._poll_log_queue)
        self.health_thread = threading.Thread(target=self._health_check_loop, daemon=True)
        self.health_thread.start()

        # 起動直後の処理
        self.root.after(200, self._initial_startup_sequence)

    def _load_profile(self) -> Dict[str, Any]:
        if os.path.isfile(self.profile_path):
            try:
                with open(self.profile_path, "r", encoding="utf-8", errors="replace") as f:
                    return parse_profile_bat(f.read())
            except Exception:
                pass
        return parse_profile_bat("")

    def _save_profile(self) -> None:
        content = serialize_profile_bat(self.profile)
        with open(self.profile_path, "wb") as f:
            f.write(content.encode("utf-8"))
        self.profile_summary_var.set(self._format_profile_summary())

    def _format_profile_summary(self) -> str:
        p_name = self.profile.get("PROFILE_NAME", "16GB (High)")
        ncmoe = self.profile.get("NCMOE", 9)
        vision = "ON (GPU)" if str(self.profile.get("MMPROJ_ARGS", "")).strip() else "OFF (FlashAttn優先)"
        return f"プロファイル: {p_name}  |  -ncmoe: {ncmoe}  |  Vision: {vision}"

    def _setup_styles(self) -> None:
        style = self.ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(".", background="#0f1420", foreground="#e2e8f0", font=("Yu Gothic UI", 9))
        style.configure("TFrame", background="#0f1420")
        style.configure("Card.TFrame", background="#161f30", relief="flat")
        style.configure("Header.TFrame", background="#1c273d")

        style.configure("TLabel", background="#0f1420", foreground="#e2e8f0", font=("Yu Gothic UI", 9))
        style.configure("Card.TLabel", background="#161f30", foreground="#e2e8f0", font=("Yu Gothic UI", 9))
        style.configure("CardTitle.TLabel", background="#161f30", foreground="#38bdf8", font=("Yu Gothic UI", 10, "bold"))
        style.configure("MetricBig.TLabel", background="#161f30", foreground="#4ade80", font=("Consolas", 13, "bold"))
        style.configure("MetricSub.TLabel", background="#161f30", foreground="#94a3b8", font=("Yu Gothic UI", 9))

        style.configure(
            "TNotebook", background="#0f1420", borderwidth=0, tabmargins=[2, 2, 2, 0]
        )
        style.configure(
            "TNotebook.Tab",
            background="#1c273d",
            foreground="#94a3b8",
            padding=[12, 5],
            font=("Yu Gothic UI", 9, "bold"),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", "#2563eb"), ("active", "#283552")],
            foreground=[("selected", "#ffffff"), ("active", "#e2e8f0")],
        )

        style.configure(
            "Treeview",
            background="#111827",
            foreground="#e2e8f0",
            fieldbackground="#111827",
            rowheight=24,
            borderwidth=0,
            font=("Yu Gothic UI", 9),
        )
        style.configure(
            "Treeview.Heading",
            background="#1e293b",
            foreground="#cbd5e1",
            font=("Yu Gothic UI", 9, "bold"),
            relief="flat",
        )
        style.map(
            "Treeview",
            background=[("selected", "#1d4ed8")],
            foreground=[("selected", "#ffffff")],
        )

    def _make_btn(
        self,
        parent: Any,
        text: str,
        command: Any,
        bg: str = "#2563eb",
        fg: str = "#ffffff",
        width: Optional[int] = None,
    ) -> Any:
        btn = self.tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground="#3b82f6",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            padx=10,
            pady=4,
            cursor="hand2",
            font=("Yu Gothic UI", 9, "bold"),
        )
        if width:
            btn.configure(width=width)
        return btn

    def _build_ui(self) -> None:
        tk = self.tk
        ttk = self.ttk

        # ======================================================================
        # 1. 最上部タイトル & グローバルアクションバー
        # ======================================================================
        top_bar = tk.Frame(self.root, bg="#172033", padx=12, pady=8)
        top_bar.pack(fill="x")

        title_lbl = tk.Label(
            top_bar,
            text="泥濘 (Deinei) AI Server Launcher",
            bg="#172033",
            fg="#f8fafc",
            font=("Yu Gothic UI", 13, "bold"),
        )
        title_lbl.pack(side="left")

        prof_lbl = tk.Label(
            top_bar,
            textvariable=self.profile_summary_var,
            bg="#172033",
            fg="#38bdf8",
            font=("Yu Gothic UI", 9, "bold"),
            padx=14,
        )
        prof_lbl.pack(side="left")

        # 右側ボタン群
        self._make_btn(top_bar, "全サーバー停止", self.stop_all_servers, bg="#b91c1c").pack(side="right", padx=4)
        self._make_btn(top_bar, "全サーバー起動 / 再起動", self.restart_all_servers, bg="#15803d").pack(side="right", padx=4)
        self._make_btn(top_bar, "プロファイル設定", self.open_profile_dialog, bg="#334155").pack(side="right", padx=4)
        self._make_btn(top_bar, "Doctor 接続診断", self.open_doctor_dialog, bg="#334155").pack(side="right", padx=4)
        self._make_btn(top_bar, "WebUI を開く (8080)", self.open_webui_in_browser, bg="#2563eb").pack(side="right", padx=4)

        # ======================================================================
        # 2. サーバー稼働ランプ & ネットワーク公開モード制御バー
        # ======================================================================
        ctrl_frame = tk.Frame(self.root, bg="#131b2e", padx=12, pady=6)
        ctrl_frame.pack(fill="x", padx=10, pady=(6, 4))

        # 2-A: 稼働ステータスバッジ行
        status_row = tk.Frame(ctrl_frame, bg="#131b2e")
        status_row.pack(fill="x", pady=(0, 6))

        tk.Label(
            status_row, text="稼働状態:", bg="#131b2e", fg="#94a3b8", font=("Yu Gothic UI", 9, "bold")
        ).pack(side="left", padx=(0, 8))

        self.status_badges: Dict[str, Any] = {}
        badge_defs = [
            ("llm", "LLM 推論 (8080): OFFLINE"),
            ("flask", "Data / RAG (5000): OFFLINE"),
            ("tts", "TTS 音声合成 (5001): OFFLINE"),
            ("gw", "Gateway (8090): DISABLED"),
        ]
        for key, init_txt in badge_defs:
            lbl = tk.Label(
                status_row,
                text=init_txt,
                bg="#1e293b",
                fg="#94a3b8",
                font=("Consolas", 9, "bold"),
                padx=10,
                pady=3,
            )
            lbl.pack(side="left", padx=4)
            self.status_badges[key] = lbl

        # 2-B: ネットワーク公開モード切替行
        net_row = tk.Frame(ctrl_frame, bg="#131b2e")
        net_row.pack(fill="x")

        tk.Label(
            net_row, text="公開モード:", bg="#131b2e", fg="#94a3b8", font=("Yu Gothic UI", 9, "bold")
        ).pack(side="left", padx=(0, 8))

        modes = [
            ("ローカルのみ (PC専用)", "local"),
            ("LAN 公開 (同一Wi-Fiスマホ接続)", "lan"),
            ("Tailscale 公開 (外出先VPN)", "tailscale"),
        ]
        for label_txt, val in modes:
            rb = tk.Radiobutton(
                net_row,
                text=label_txt,
                variable=self.gateway_mode_var,
                value=val,
                command=self._on_gateway_mode_changed,
                bg="#131b2e",
                fg="#e2e8f0",
                selectcolor="#1e293b",
                activebackground="#131b2e",
                activeforeground="#38bdf8",
                font=("Yu Gothic UI", 9),
            )
            rb.pack(side="left", padx=6)

        https_cb = tk.Checkbutton(
            net_row,
            text="Tailscale HTTPS",
            variable=self.tailscale_https_var,
            command=self._on_gateway_mode_changed,
            bg="#131b2e",
            fg="#cbd5e1",
            selectcolor="#1e293b",
            activebackground="#131b2e",
            activeforeground="#38bdf8",
            font=("Yu Gothic UI", 9),
        )
        https_cb.pack(side="left", padx=6)

        self._make_btn(net_row, "QRコード表示", self.open_qr_dialog, bg="#0284c7").pack(side="right", padx=4)
        self._make_btn(net_row, "接続URLコピー", self.copy_gateway_url, bg="#334155").pack(side="right", padx=4)

        url_entry = tk.Entry(
            net_row,
            textvariable=self.gateway_url_var,
            readonlybackground="#0f172a",
            fg="#38bdf8",
            relief="flat",
            state="readonly",
            font=("Consolas", 9),
            width=42,
        )
        url_entry.pack(side="right", padx=6, ipady=3)

        # ======================================================================
        # 3. 中段: リアルタイム重要指標 (Live Telemetry) & RAG 類似度インスペクター
        # ======================================================================
        mid_container = tk.Frame(self.root, bg="#0f1420")
        mid_container.pack(fill="x", padx=10, pady=4)

        cards_row = tk.Frame(mid_container, bg="#0f1420")
        cards_row.pack(fill="x", pady=(0, 6))
        cards_row.columnconfigure(0, weight=1)
        cards_row.columnconfigure(1, weight=1)

        # 3-A: 左カード (LLM & KVキャッシュ性能)
        llm_card = tk.Frame(cards_row, bg="#161f30", padx=12, pady=8)
        llm_card.grid(row=0, column=0, sticky="nsew", padx=(0, 4))

        tk.Label(
            llm_card,
            text="[LLM 推論速度 & KVキャッシュ再利用メトリクス]",
            bg="#161f30",
            fg="#38bdf8",
            font=("Yu Gothic UI", 10, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))

        tk.Label(llm_card, text="生成速度 (TPS):", bg="#161f30", fg="#94a3b8", font=("Yu Gothic UI", 9)).grid(
            row=1, column=0, sticky="w"
        )
        tps_box = tk.Frame(llm_card, bg="#161f30")
        tps_box.grid(row=1, column=1, sticky="w", padx=8)
        tk.Label(
            tps_box, textvariable=self.metric_tps_var, bg="#161f30", fg="#4ade80", font=("Consolas", 12, "bold")
        ).pack(side="left")
        tk.Label(
            tps_box, textvariable=self.metric_prompt_eval_var, bg="#161f30", fg="#94a3b8", font=("Consolas", 9), padx=8
        ).pack(side="left")

        tk.Label(llm_card, text="コンテキスト使用量:", bg="#161f30", fg="#94a3b8", font=("Yu Gothic UI", 9)).grid(
            row=2, column=0, sticky="w", pady=2
        )
        tk.Label(
            llm_card, textvariable=self.metric_ctx_var, bg="#161f30", fg="#f8fafc", font=("Consolas", 10, "bold")
        ).grid(row=2, column=1, sticky="w", padx=8, pady=2)

        tk.Label(llm_card, text="KVキャッシュ再利用:", bg="#161f30", fg="#94a3b8", font=("Yu Gothic UI", 9)).grid(
            row=3, column=0, sticky="w", pady=2
        )
        tk.Label(
            llm_card, textvariable=self.metric_kv_reuse_var, bg="#161f30", fg="#38bdf8", font=("Yu Gothic UI", 9, "bold")
        ).grid(row=3, column=1, sticky="w", padx=8, pady=2)

        tk.Label(llm_card, text="プレフィックス差分:", bg="#161f30", fg="#94a3b8", font=("Yu Gothic UI", 9)).grid(
            row=4, column=0, sticky="w"
        )
        tk.Label(
            llm_card, textvariable=self.metric_kv_mismatch_var, bg="#161f30", fg="#cbd5e1", font=("Yu Gothic UI", 9)
        ).grid(row=4, column=1, sticky="w", padx=8)

        # 3-B: 右カード (Web検索ツール & TTS 音声合成)
        tts_card = tk.Frame(cards_row, bg="#161f30", padx=12, pady=8)
        tts_card.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        tts_card.columnconfigure(1, weight=1)

        tk.Label(
            tts_card,
            text="[Web検索ツール & TTS 音声合成ステータス]",
            bg="#161f30",
            fg="#38bdf8",
            font=("Yu Gothic UI", 10, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))

        tk.Label(tts_card, text="Web検索トグル:", bg="#161f30", fg="#94a3b8", font=("Yu Gothic UI", 9)).grid(
            row=1, column=0, sticky="w"
        )
        search_ctrl = tk.Frame(tts_card, bg="#161f30")
        search_ctrl.grid(row=1, column=1, sticky="w", padx=8)
        self.search_toggle_lbl = tk.Label(
            search_ctrl,
            textvariable=self.metric_search_toggle_var,
            bg="#161f30",
            fg="#4ade80",
            font=("Consolas", 10, "bold"),
        )
        self.search_toggle_lbl.pack(side="left")

        tk.Label(tts_card, text="直近の検索キーワード:", bg="#161f30", fg="#94a3b8", font=("Yu Gothic UI", 9)).grid(
            row=2, column=0, sticky="w", pady=2
        )
        tk.Label(
            tts_card,
            textvariable=self.metric_search_query_var,
            bg="#161f30",
            fg="#facc15",
            font=("Yu Gothic UI", 9, "bold"),
            anchor="w",
        ).grid(row=2, column=1, sticky="ew", padx=8, pady=2)

        tk.Label(tts_card, text="TTS 合成状態:", bg="#161f30", fg="#94a3b8", font=("Yu Gothic UI", 9)).grid(
            row=3, column=0, sticky="w", pady=2
        )
        tk.Label(
            tts_card,
            textvariable=self.metric_tts_state_var,
            bg="#161f30",
            fg="#c084fc",
            font=("Yu Gothic UI", 9, "bold"),
        ).grid(row=3, column=1, sticky="w", padx=8, pady=2)

        tk.Label(tts_card, text="直近の読み上げセリフ:", bg="#161f30", fg="#94a3b8", font=("Yu Gothic UI", 9)).grid(
            row=4, column=0, sticky="nw"
        )
        tk.Label(
            tts_card,
            textvariable=self.metric_tts_text_var,
            bg="#161f30",
            fg="#e2e8f0",
            font=("Yu Gothic UI", 9),
            wraplength=420,
            justify="left",
            anchor="w",
        ).grid(row=4, column=1, sticky="ew", padx=8)

        # 3-C: RAG 記憶検索 & コサイン類似度インスペクター (テーブル表示)
        rag_card = tk.Frame(mid_container, bg="#161f30", padx=12, pady=8)
        rag_card.pack(fill="x")

        rag_head = tk.Frame(rag_card, bg="#161f30")
        rag_head.pack(fill="x", pady=(0, 4))

        tk.Label(
            rag_head,
            text="[RAG 記憶検索 & 親子リトリーバル コサイン類似度インスペクター]",
            bg="#161f30",
            fg="#38bdf8",
            font=("Yu Gothic UI", 10, "bold"),
        ).pack(side="left")

        tk.Label(
            rag_head,
            textvariable=self.rag_summary_var,
            bg="#161f30",
            fg="#cbd5e1",
            font=("Yu Gothic UI", 9),
            padx=12,
        ).pack(side="left")

        self._make_btn(rag_head, "履歴クリア", self._clear_rag_table, bg="#334155").pack(side="right")

        tree_frame = tk.Frame(rag_card, bg="#111827")
        tree_frame.pack(fill="x")

        cols = ("status", "category", "sim", "dist", "key", "content")
        self.rag_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=5)
        self.rag_tree.heading("status", text="判定 / 順位")
        self.rag_tree.heading("category", text="記憶種別")
        self.rag_tree.heading("sim", text="コサイン類似度")
        self.rag_tree.heading("dist", text="コサイン距離")
        self.rag_tree.heading("key", text="検索キー (子要素)")
        self.rag_tree.heading("content", text="記憶テキスト / 親エピソード抜粋")

        self.rag_tree.column("status", width=115, anchor="w", stretch=False)
        self.rag_tree.column("category", width=95, anchor="center", stretch=False)
        self.rag_tree.column("sim", width=105, anchor="center", stretch=False)
        self.rag_tree.column("dist", width=95, anchor="center", stretch=False)
        self.rag_tree.column("key", width=165, anchor="w", stretch=False)
        self.rag_tree.column("content", width=520, anchor="w", stretch=True)

        self.rag_tree.tag_configure("adopted_top", foreground="#4ade80", background="#132a20")
        self.rag_tree.tag_configure("adopted", foreground="#86efac")
        self.rag_tree.tag_configure("worldline_out", foreground="#facc15")
        self.rag_tree.tag_configure("rejected", foreground="#64748b")

        rag_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.rag_tree.yview)
        self.rag_tree.configure(yscrollcommand=rag_scroll.set)
        self.rag_tree.pack(side="left", fill="x", expand=True)
        rag_scroll.pack(side="right", fill="y")

        self.rag_tree.bind("<<TreeviewSelect>>", self._on_rag_row_selected)

        detail_entry = tk.Entry(
            rag_card,
            textvariable=self.rag_selected_detail_var,
            readonlybackground="#0f172a",
            fg="#cbd5e1",
            relief="flat",
            state="readonly",
            font=("Yu Gothic UI", 9),
        )
        detail_entry.pack(fill="x", pady=(4, 0), ipady=3)

        # ======================================================================
        # 4. 下段: タブ切り替え式コンソールログビューア
        # ======================================================================
        log_container = tk.Frame(self.root, bg="#0f1420")
        log_container.pack(fill="both", expand=True, padx=10, pady=(2, 10))

        log_ctrl = tk.Frame(log_container, bg="#0f1420")
        log_ctrl.pack(fill="x", pady=(0, 2))

        tk.Label(
            log_ctrl,
            text="[サーバーコンソールログ統合ビューア]",
            bg="#0f1420",
            fg="#38bdf8",
            font=("Yu Gothic UI", 10, "bold"),
        ).pack(side="left")

        self._make_btn(log_ctrl, "表示タブのログをクリア", self._clear_current_log_tab, bg="#334155").pack(
            side="right", padx=4
        )

        tk.Checkbutton(
            log_ctrl,
            text="自動スクロール",
            variable=self.autoscroll_var,
            bg="#0f1420",
            fg="#cbd5e1",
            selectcolor="#1e293b",
            activebackground="#0f1420",
            font=("Yu Gothic UI", 9),
        ).pack(side="right", padx=8)

        tk.Checkbutton(
            log_ctrl,
            text="ノイズログ除外 (/health・静的GET・内部スロットログ等)",
            variable=self.filter_noise_var,
            bg="#0f1420",
            fg="#cbd5e1",
            selectcolor="#1e293b",
            activebackground="#0f1420",
            font=("Yu Gothic UI", 9),
        ).pack(side="right", padx=8)

        self.notebook = ttk.Notebook(log_container)
        self.notebook.pack(fill="both", expand=True)

        self.log_widgets: Dict[str, Any] = {}
        tab_defs = [
            ("all", "統合イベント (All)"),
            ("llm", "LLM Server (8080)"),
            ("flask", "Data / RAG (5000)"),
            ("tts", "TTS Server (5001)"),
            ("gw", "Gateway (8090)"),
        ]
        for key, tab_title in tab_defs:
            frame = tk.Frame(self.notebook, bg="#090d16")
            self.notebook.add(frame, text=tab_title)

            txt = tk.Text(
                frame,
                bg="#090d16",
                fg="#e2e8f0",
                insertbackground="#e2e8f0",
                relief="flat",
                 wrap="word",
                font=("Consolas", 9),
                state="disabled",
            )
            sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
            txt.configure(yscrollcommand=sb.set)
            txt.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")

            # ログ色分けタグ
            txt.tag_configure("src_llm", foreground="#38bdf8")
            txt.tag_configure("src_flask", foreground="#4ade80")
            txt.tag_configure("src_tts", foreground="#facc15")
            txt.tag_configure("src_gw", foreground="#c084fc")
            txt.tag_configure("src_sys", foreground="#f8fafc")
            txt.tag_configure("err", foreground="#f87171")

            self.log_widgets[key] = txt

    # ==========================================================================
    # 起動・プロセス制御ロジック
    # ==========================================================================
    def _initial_startup_sequence(self) -> None:
        if self.args.doctor:
            self.open_doctor_dialog()
            if self.args.no_autostart:
                return

        open_browser = not self.args.no_browser and not self.args.doctor
        if not self.args.no_autostart:
            threading.Thread(
                target=self._start_all_servers_worker,
                kwargs={"force_restart": True, "open_browser_after_cleanup": open_browser},
                daemon=True,
            ).start()
        elif open_browser:
            self.open_splash_in_browser()

    def _emit_sys_log(self, msg: str) -> None:
        self.log_queue.put(("sys", msg))

    def _start_all_servers_worker(
        self, force_restart: bool = False, open_browser_after_cleanup: bool = False
    ) -> None:
        with self.proc_lock:
            has_own_procs = any(self.procs.get(k) is not None for k in ("llm", "flask", "tts"))

            if force_restart or not has_own_procs:
                if force_restart and has_own_procs:
                    self._emit_sys_log("[INFO] 既存のサーバープロセスを停止しています...")
                else:
                    self._emit_sys_log("[INFO] 起動時初期化: 残存孤児サーバープロセスのクリーンアップを実行しています...")
                self._stop_all_locked()
                cleaned = cleanup_orphan_servers()
                for c_msg in cleaned:
                    self._emit_sys_log(f"[INFO] 残存プロセスクリーンアップ: {c_msg}")
                time.sleep(0.8)

                if open_browser_after_cleanup:
                    self.open_splash_in_browser()

                need_llm, need_flask, need_tts = True, True, True
            else:
                llm_ok, llm_msg = check_http_health("127.0.0.1", 8080, "/health")
                flask_ok, flask_msg = check_http_health("127.0.0.1", 5000, "/health")
                tts_ok, tts_msg = check_http_health("127.0.0.1", 5001, "/health")

                llm_alive = llm_ok or ("LOADING" in llm_msg)
                flask_alive = flask_ok or ("LOADING" in flask_msg)
                tts_alive = tts_ok or ("LOADING" in tts_msg)

                if llm_alive and flask_alive and tts_alive:
                    self._emit_sys_log("[OK] 3つのバックエンドサーバー (8080, 5000, 5001) は既に稼働またはロード中です。")
                    self._start_gateway_locked()
                    return

                need_llm = not llm_alive
                need_flask = not flask_alive
                need_tts = not tts_alive
                orphan_ports: List[int] = []
                if need_llm:
                    if self.procs.get("llm") is not None:
                        stop_popen_process(self.procs["llm"])
                        self.procs["llm"] = None
                    orphan_ports.append(8080)
                if need_flask:
                    if self.procs.get("flask") is not None:
                        stop_popen_process(self.procs["flask"])
                        self.procs["flask"] = None
                    orphan_ports.append(5000)
                if need_tts:
                    if self.procs.get("tts") is not None:
                        stop_popen_process(self.procs["tts"])
                        self.procs["tts"] = None
                    orphan_ports.append(5001)
                if self._gateway_mode in ("lan", "tailscale"):
                    if self.procs.get("gw") is not None:
                        stop_popen_process(self.procs["gw"])
                        self.procs["gw"] = None
                    orphan_ports.append(self.gateway_port or 8090)

                cleaned = cleanup_orphan_servers(target_ports=orphan_ports)
                for c_msg in cleaned:
                    self._emit_sys_log(f"[INFO] 残存プロセスクリーンアップ: {c_msg}")
                if cleaned:
                    time.sleep(0.8)

            self._emit_sys_log(f"[INFO] バックエンド Python 実行環境: {self.py_exe}")
            # 1. Flask Data Server (5000) を最優先で起動し、Ruri埋め込みモデルのロード完了を待機
            #    (LLM --no-mmap 約17GB / TTS 約8GB との同時コミットチャージ衝突による os error 1455 を回避)
            if need_flask:
                self._start_flask_locked()
                for _ in range(40):
                    time.sleep(0.5)
                    f_proc = self.procs.get("flask")
                    if f_proc is not None and f_proc.poll() is not None:
                        break
                    f_ok, f_msg = check_http_health("127.0.0.1", 5000, "/health")
                    if f_ok or "ERROR" in f_msg:
                        break
            # 2. LLM Server (8080)
            if need_llm:
                self._start_llm_locked()
                time.sleep(1.0)
            # 3. TTS Server (5001)
            if need_tts:
                self._start_tts_locked()
                time.sleep(1.0)
            # 4. Gateway (8090, 選択時のみ)
            self._start_gateway_locked()

    def _start_llm_locked(self) -> None:
        ensure_jinja_template(self.root_dir)
        sandbox_dir = os.path.join(self.root_dir, "llama-b9873-bin-win-cuda-13.3-x64sandbox")
        llama_exe = os.path.join(sandbox_dir, "llama-server.exe")
        if not os.path.isfile(llama_exe):
            self._emit_sys_log(f"[ERROR] llama-server.exe が見つかりません: {llama_exe}")
            return

        model_path = find_llm_model(self.root_dir)
        ncmoe = str(self.profile.get("NCMOE", 9))
        mmproj_raw = str(self.profile.get("MMPROJ_ARGS", "")).strip()

        cmd = [
            llama_exe,
            "-m",
            model_path,
        ]
        if mmproj_raw:
            cmd.extend(mmproj_raw.split())
        cmd.extend(
            [
                "--host",
                "127.0.0.1",
                "--no-slots",
                "--no-mmap",
                "-fit",
                "off",
                "-c",
                "16384",
                "-ngl",
                "99",
                "-ncmoe",
                ncmoe,
                "--flash-attn",
                "on",
                "--cache-reuse",
                "256",
                "-np",
                "1",
                "--parallel",
                "1",
                "--path",
                "./custom_public",
                "--port",
                "8080",
                "--chat-template-file",
                "gemma_chat_template.jinja",
            ]
        )

        self._emit_sys_log(
            f"[INFO] [2/3] LLM Server (8080) を起動中... (Model: {os.path.basename(model_path)}, -ncmoe {ncmoe})"
        )
        self._spawn_managed_process("llm", cmd, cwd=sandbox_dir)

    def _start_tts_locked(self) -> None:
        tts_dir = os.path.join(self.root_dir, "backend", "Irodori-TTS")
        tts_script = os.path.join(tts_dir, "tts_server.py")
        if not os.path.isfile(tts_script):
            self._emit_sys_log(f"[ERROR] tts_server.py が見つかりません: {tts_script}")
            return

        cmd = [self.py_exe, "-X", "utf8", "-u", tts_script]
        self._emit_sys_log("[INFO] [3/3] TTS Server (5001) を起動中...")
        self._spawn_managed_process("tts", cmd, cwd=tts_dir)

    def _start_flask_locked(self) -> None:
        data_dir = os.path.join(self.root_dir, "backend", "data")
        flask_script = os.path.join(data_dir, "server.py")
        if not os.path.isfile(flask_script):
            self._emit_sys_log(f"[ERROR] server.py が見つかりません: {flask_script}")
            return

        cmd = [self.py_exe, "-X", "utf8", "-u", flask_script]
        self._emit_sys_log("[INFO] [1/3] Data / RAG Server (5000) を起動中... (最優先起動)")
        self._spawn_managed_process("flask", cmd, cwd=data_dir)

    def _start_gateway_locked(self) -> None:
        # 既存の Gateway プロセスがあれば先に停止
        if self.procs.get("gw") is not None:
            stop_popen_process(self.procs["gw"])
            self.procs["gw"] = None

        mode = self._gateway_mode
        if mode == "local":
            if not self.is_shutting_down:
                try:
                    self.root.after(0, lambda: self.gateway_url_var.set("ローカル専用モード (http://127.0.0.1:8080)"))
                except Exception:
                    pass
            self._emit_sys_log("[INFO] 公開モード: ローカルのみ (Gateway 停止)")
            return

        net_script = os.path.join(self.root_dir, "backend", "data", "deinei_net.py")
        cmd = [self.py_exe, "-X", "utf8", "-u", net_script, "serve", "--no-qr"]
        if mode == "lan":
            cmd.append("--lan")
        elif mode == "tailscale":
            cmd.append("--tailscale")
            if self._tailscale_https:
                cmd.append("--https")
        if self.args.port:
            cmd.extend(["--port", str(self.args.port)])
        if self.args.token:
            cmd.extend(["--token", str(self.args.token)])

        # 泥濘自身の Gateway 残存プロセスがあれば事前に解放 (他社サービスは除外される)
        gw_target_port = getattr(self.args, "port", None) or 8090
        cleaned = cleanup_orphan_servers(target_ports=[gw_target_port])
        for c_msg in cleaned:
            self._emit_sys_log(f"[INFO] 残存 Gateway クリーンアップ: {c_msg}")

        self._emit_sys_log(f"[INFO] 認証ゲートウェイを起動中 (モード: {mode.upper()})...")
        self._spawn_managed_process("gw", cmd, cwd=os.path.join(self.root_dir, "backend", "data"))

    def _spawn_managed_process(self, key: str, cmd: List[str], cwd: str) -> None:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                creationflags=CREATE_NO_WINDOW,
            )
            self.procs[key] = proc
            t = threading.Thread(target=self._reader_thread, args=(key, proc), daemon=True)
            t.start()
        except Exception as e:
            self._emit_sys_log(f"[ERROR] {key} プロセスの起動に失敗しました: {e}")

    def _reader_thread(self, key: str, proc: subprocess.Popen) -> None:
        try:
            assert proc.stdout is not None
            for raw_bytes in iter(proc.stdout.readline, b""):
                if not raw_bytes:
                    break
                line = raw_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
                self.log_queue.put((key, line))
        except Exception:
            pass
        finally:
            rc = proc.poll()
            if not self.is_shutting_down and self.procs.get(key) is proc:
                if rc is not None and rc != 0:
                    self.log_queue.put((key, f"[ERROR] プロセス '{key}' が異常終了しました (exit code: {rc})"))
                self.log_queue.put(("sys", f"[INFO] プロセス '{key.upper()}' が終了しました (code: {rc})"))

    def _stop_all_locked(self) -> None:
        for key in ("gw", "flask", "tts", "llm"):
            proc = self.procs.get(key)
            if proc is not None:
                stop_popen_process(proc)
                self.procs[key] = None

    def stop_all_servers(self) -> None:
        def _worker():
            with self.proc_lock:
                self._emit_sys_log("[INFO] 全サーバーを停止しています...")
                self._stop_all_locked()
                cleanup_orphan_servers()
                self._emit_sys_log("[OK] 全サーバーを停止しました。")

        threading.Thread(target=_worker, daemon=True).start()

    def restart_all_servers(self) -> None:
        threading.Thread(target=self._start_all_servers_worker, args=(True,), daemon=True).start()

    def _on_gateway_mode_changed(self) -> None:
        self._gateway_mode = self.gateway_mode_var.get()
        self._tailscale_https = bool(self.tailscale_https_var.get())

        def _worker():
            with self.proc_lock:
                self._start_gateway_locked()

        threading.Thread(target=_worker, daemon=True).start()

    # ==========================================================================
    # ログ解析・テレメトリ更新ループ
    # ==========================================================================
    def _poll_log_queue(self) -> None:
        processed = 0
        while processed < 120:
            try:
                source, line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            processed += 1
            self._process_single_log(source, line)

        if not self.is_shutting_down:
            self.root.after(80, self._poll_log_queue)

    def _process_single_log(self, source: str, raw_line: str) -> None:
        clean_line = strip_ansi(raw_line)
        if not clean_line.strip():
            return

        # 1. 各ソース別のリアルタイム指標パース
        if source == "llm":
            info = parse_llm_log_line(clean_line)
            if "eval_tps" in info:
                tps = info["eval_tps"]
                toks = info.get("eval_tokens", 0)
                ms = info.get("eval_ms", 0.0)
                self.metric_tps_var.set(f"{tps:.2f} t/s ({toks} tok / {ms:.0f} ms)")
            if "prompt_tps" in info:
                ptps = info["prompt_tps"]
                ptoks = info.get("prompt_eval_tokens", 0)
                pms = info.get("prompt_eval_ms", 0.0)
                self.metric_prompt_eval_var.set(f"Prompt: {ptps:.1f} t/s ({pms:.0f} ms / {ptoks} tok)")
            if "n_tokens" in info:
                ntok = info["n_tokens"]
                pct = min(100.0, ntok / 16384.0 * 100.0)
                self.metric_ctx_var.set(f"{ntok:,} / 16,384 tok ({pct:.1f}%)")

        elif source == "flask":
            info = parse_flask_log_line(clean_line, current_rag_section=self.current_rag_section)
            if "rag_section" in info:
                self.current_rag_section = info["rag_section"]

            if "token_cache" in info:
                tc = info["token_cache"]
                if tc.get("is_initial"):
                    curr = tc["curr_tokens"]
                    self.metric_kv_reuse_var.set(f"初回ターン (総トークン数: {curr:,} tok)")
                    self.metric_kv_mismatch_var.set("食い違い位置: なし (初回ターン)")
                    pct = min(100.0, curr / 16384.0 * 100.0)
                    self.metric_ctx_var.set(f"{curr:,} / 16,384 tok ({pct:.1f}%)")
                else:
                    prev_t = tc["prev_tokens"]
                    curr_t = tc["curr_tokens"]
                    reused = tc["reused_tokens"]
                    rpct = tc["reuse_pct"]
                    self.metric_kv_reuse_var.set(
                        f"{reused:,} tok 再利用 ({rpct:.1f}%)  [前回: {prev_t:,} -> 今回: {curr_t:,} tok]"
                    )
                    pct = min(100.0, curr_t / 16384.0 * 100.0)
                    self.metric_ctx_var.set(f"{curr_t:,} / 16,384 tok ({pct:.1f}%)")

            if "token_cache_mismatch_pos" in info:
                pos = info["token_cache_mismatch_pos"]
                self.metric_kv_mismatch_var.set(f"食い違い位置: {pos:,} トークン目")

            if "search_enabled" in info:
                enabled = info["search_enabled"]
                self.metric_search_toggle_var.set("ON" if enabled else "OFF")
                self.search_toggle_lbl.configure(fg="#4ade80" if enabled else "#94a3b8")

            if "search_query" in info:
                q = info["search_query"]
                cnt = info.get("search_result_count")
                if cnt is not None:
                    self.metric_search_query_var.set(f"'{q}' ({cnt}件取得)")
                else:
                    self.metric_search_query_var.set(f"'{q}'")

            if "rag_query_header" in info:
                qh = info["rag_query_header"]
                self.rag_candidates_buffer.clear()
                self.rag_top6_ranks.clear()
                self._clear_rag_table()
                self.rag_summary_var.set(
                    f"クエリ: 「{qh['query'][:28]}」 | コレクション総数: {qh['collection_count']}件"
                )

            if "rag_candidate" in info:
                cand = info["rag_candidate"]
                self.rag_candidates_buffer.append(cand)
                self._insert_rag_candidate_row(cand)

            if "rag_final_counts" in info:
                fc = info["rag_final_counts"]
                cur_sum = self.rag_summary_var.get().split(" | 採用:")[0]
                self.rag_summary_var.set(
                    f"{cur_sum} | 採用: Episode {fc['episode_count']}件 / Fact {fc['fact_count']}件"
                )

            if "rag_top6_item" in info:
                t6 = info["rag_top6_item"]
                self._highlight_top6_in_tree(t6)

        elif source == "tts":
            info = parse_tts_log_line(clean_line)
            if info.get("tts_status") == "SYNTHESIZING":
                self.metric_tts_state_var.set("音声合成中 (SYNTHESIZING)")
            elif info.get("tts_completed"):
                self.metric_tts_state_var.set("合成完了 (ONLINE / 待機中)")

            if "clean_text" in info:
                ctext = info["clean_text"]
                ccount = info.get("clean_char_count", len(ctext))
                self.metric_tts_text_var.set(f"({ccount}文字) {ctext}")

        elif source == "gw":
            info = parse_gateway_log_line(clean_line)
            if "gateway_url" in info:
                self.gateway_url_var.set(info["gateway_url"])

        # 2. ノイズログフィルタ判定
        if self.filter_noise_var.get() and is_noise_log_line(source, clean_line):
            return

        # 3. テキストウィジェットへの出力
        ts_str = datetime.now().strftime("%H:%M:%S")
        tag_map = {
            "llm": ("LLM", "src_llm"),
            "flask": ("RAG", "src_flask"),
            "tts": ("TTS", "src_tts"),
            "gw": ("GW ", "src_gw"),
            "sys": ("SYS", "src_sys"),
        }
        badge, color_tag = tag_map.get(source, ("LOG", "src_sys"))
        if "[ERROR]" in clean_line or "Traceback" in clean_line or "Exception:" in clean_line:
            color_tag = "err"

        # 複数行の === 枠線などは個別のタブには出すが統合タブではスリム化
        is_divider = set(clean_line.strip()) <= {"=", "-"} and len(clean_line.strip()) >= 15

        if source in self.log_widgets:
            self._append_text_widget(self.log_widgets[source], f"{ts_str}  {clean_line}\n", color_tag)

        if not is_divider:
            self._append_text_widget(
                self.log_widgets["all"], f"{ts_str} [{badge}] {clean_line}\n", color_tag
            )

    def _append_text_widget(self, widget: Any, text: str, tag: str) -> None:
        widget.configure(state="normal")
        widget.insert("end", text, tag)
        # メモリ肥大化防止: 最大1500行に維持
        line_count = int(widget.index("end-1c").split(".")[0])
        if line_count > 1500:
            widget.delete("1.0", f"{line_count - 1200}.0")
        if self.autoscroll_var.get():
            widget.see("end")
        widget.configure(state="disabled")

    def _insert_rag_candidate_row(self, cand: Dict[str, Any]) -> None:
        status_lbl = cand["status_label"]
        cat = cand["category"]
        sim_str = f"{cand['similarity']:.4f}"
        dist_str = f"{cand['distance']:.4f}"
        key_str = cand["key"]
        content_str = cand["content"]

        if cand["adopted"]:
            row_tag = "adopted"
        elif cand["reason"] == "世界線外":
            row_tag = "worldline_out"
        else:
            row_tag = "rejected"

        self.rag_tree.insert(
            "",
            "end",
            values=(status_lbl, cat, sim_str, dist_str, key_str, content_str),
            tags=(row_tag,),
        )

    def _highlight_top6_in_tree(self, top6_item: Dict[str, Any]) -> None:
        rank = top6_item["rank"]
        cat = top6_item["category"]
        sim = top6_item["similarity"]
        for item_id in self.rag_tree.get_children():
            vals = self.rag_tree.item(item_id, "values")
            if not vals or len(vals) < 6:
                continue
            row_cat = vals[1]
            try:
                row_sim = float(vals[2])
            except ValueError:
                continue
            if row_cat == cat and abs(row_sim - sim) < 0.002 and "[OK]" in vals[0]:
                new_vals = (f"[OK] 採用 #{rank}", vals[1], vals[2], vals[3], vals[4], vals[5])
                self.rag_tree.item(item_id, values=new_vals, tags=("adopted_top",))
                break

    def _clear_rag_table(self) -> None:
        for item_id in self.rag_tree.get_children():
            self.rag_tree.delete(item_id)

    def _on_rag_row_selected(self, _event: Any) -> None:
        sel = self.rag_tree.selection()
        if not sel:
            return
        vals = self.rag_tree.item(sel[0], "values")
        if vals and len(vals) >= 6:
            self.rag_selected_detail_var.set(
                f"[{vals[1]} | {vals[0]} | 類似度: {vals[2]} (距離: {vals[3]})] キー: {vals[4]}  |  内容: {vals[5]}"
            )

    def _clear_current_log_tab(self) -> None:
        current_idx = self.notebook.index(self.notebook.select())
        keys = ["all", "llm", "flask", "tts", "gw"]
        if 0 <= current_idx < len(keys):
            w = self.log_widgets[keys[current_idx]]
            w.configure(state="normal")
            w.delete("1.0", "end")
            w.configure(state="disabled")

    # ==========================================================================
    # バックグラウンドヘルスチェック監視
    # ==========================================================================
    def _health_check_loop(self) -> None:
        while not self.is_shutting_down:
            llm_ok, llm_msg = check_http_health("127.0.0.1", 8080, "/health")
            flask_ok, flask_msg = check_http_health("127.0.0.1", 5000, "/health")
            tts_ok, tts_msg = check_http_health("127.0.0.1", 5001, "/health")
            if self.is_shutting_down:
                break

            mode = self._gateway_mode
            gw_proc = self.procs.get("gw")
            if mode == "local":
                gw_state = ("DISABLED", "#64748b")
            elif gw_proc is not None and gw_proc.poll() is None:
                gw_state = (f"{mode.upper()} ONLINE", "#4ade80")
            else:
                gw_state = (f"{mode.upper()} STARTING/OFF", "#facc15")

            # Flask から現在の Web検索ステータスも同期 (UI反映はメインスレッドの _update_status_badges_ui で実施)
            search_en: Optional[bool] = None
            if flask_ok and not self.is_shutting_down:
                try:
                    conn = http.client.HTTPConnection("127.0.0.1", 5000, timeout=0.6)
                    conn.request("GET", "/api/search/status")
                    r = conn.getresponse()
                    if r.status == 200:
                        data = json.loads(r.read().decode("utf-8", errors="replace"))
                        search_en = bool(data.get("search_enabled", True))
                    conn.close()
                except Exception:
                    pass

            if not self.is_shutting_down:
                try:
                    self.root.after(
                        0,
                        self._update_status_badges_ui,
                        (llm_ok, llm_msg),
                        (flask_ok, flask_msg),
                        (tts_ok, tts_msg),
                        gw_state,
                        search_en,
                    )
                except Exception:
                    break
            time.sleep(2.5)

    def _update_status_badges_ui(
        self,
        llm_st: Tuple[bool, str],
        flask_st: Tuple[bool, str],
        tts_st: Tuple[bool, str],
        gw_st: Tuple[str, str],
        search_en: Optional[bool] = None,
    ) -> None:
        def _apply(key: str, label_prefix: str, ok: bool, msg: str):
            proc = self.procs.get(key)
            proc_running = proc is not None and proc.poll() is None
            if ok:
                fg = "#4ade80"
                bg = "#142e24"
                txt = f"{label_prefix}: ONLINE"
            elif "ERROR" in msg:
                fg = "#f87171"
                bg = "#2e1619"
                txt = f"{label_prefix}: {msg}"
            elif proc_running or "LOADING" in msg:
                fg = "#facc15"
                bg = "#2e2614"
                txt = f"{label_prefix}: LOADING"
            else:
                fg = "#f87171"
                bg = "#2e1619"
                txt = f"{label_prefix}: OFFLINE"
            self.status_badges[key].configure(text=txt, fg=fg, bg=bg)

        _apply("llm", "LLM (8080)", llm_st[0], llm_st[1])
        _apply("flask", "Data/RAG (5000)", flask_st[0], flask_st[1])
        _apply("tts", "TTS (5001)", tts_st[0], tts_st[1])
        self.status_badges["gw"].configure(
            text=f"Gateway (8090): {gw_st[0]}",
            fg=gw_st[1],
            bg="#1e293b" if gw_st[0] == "DISABLED" else "#142e24",
        )
        if search_en is not None:
            self.metric_search_toggle_var.set("ON" if search_en else "OFF")
        en = self.metric_search_toggle_var.get() == "ON"
        self.search_toggle_lbl.configure(fg="#4ade80" if en else "#94a3b8")

    # ==========================================================================
    # ダイアログ & ユーティリティアクション
    # ==========================================================================
    def open_webui_in_browser(self) -> None:
        webbrowser.open("http://127.0.0.1:8080")

    def open_splash_in_browser(self) -> None:
        splash_file = os.path.join(self.root_dir, "splash.html")
        if os.path.isfile(splash_file):
            webbrowser.open(f"file:///{splash_file.replace(os.sep, '/')}")
        else:
            webbrowser.open("http://127.0.0.1:8080")

    def copy_gateway_url(self) -> None:
        url = self.gateway_url_var.get()
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self._emit_sys_log(f"[OK] 接続URLをクリップボードにコピーしました: {url}")

    def open_qr_dialog(self) -> None:
        tk = self.tk
        url = self.gateway_url_var.get().strip()
        if not url.startswith("http"):
            self.messagebox.showinfo(
                "QRコード表示",
                "現在ローカル専用モードです。\nスマートフォン等から接続する場合は、公開モードを「LAN 公開」または「Tailscale 公開」に切り替えてください。",
            )
            return

        dlg = tk.Toplevel(self.root)
        dlg.title("スマートフォン接続用 QRコード")
        dlg.geometry("420x480")
        dlg.configure(bg="#0f1420")
        dlg.transient(self.root)

        tk.Label(
            dlg,
            text="スマートフォンのカメラで読み取ってください",
            bg="#0f1420",
            fg="#f8fafc",
            font=("Yu Gothic UI", 11, "bold"),
            pady=10,
        ).pack()

        canvas_size = 320
        canvas = tk.Canvas(dlg, width=canvas_size, height=canvas_size, bg="#ffffff", highlightthickness=0)
        canvas.pack(pady=6)

        try:
            import segno

            qr = segno.make(url)
            matrix = qr.matrix
            nrows = len(matrix)
            ncols = len(matrix[0]) if nrows > 0 else 1
            border = 4
            total_units = max(nrows, ncols) + border * 2
            scale = canvas_size / total_units

            for r_idx, row in enumerate(matrix):
                for c_idx, val in enumerate(row):
                    if val:
                        x0 = (c_idx + border) * scale
                        y0 = (r_idx + border) * scale
                        x1 = x0 + scale
                        y1 = y0 + scale
                        canvas.create_rectangle(x0, y0, x1, y1, fill="#000000", outline="#000000")
        except Exception as e:
            canvas.create_text(
                canvas_size // 2,
                canvas_size // 2,
                text=f"QR描画ライブラリ(segno)未検出:\n{e}\n下記URLを直接入力してください",
                fill="#000000",
                font=("Yu Gothic UI", 9),
            )

        url_ent = tk.Entry(
            dlg,
            bg="#1e293b",
            fg="#38bdf8",
            relief="flat",
            font=("Consolas", 9),
            justify="center",
        )
        url_ent.insert(0, url)
        url_ent.configure(state="readonly")
        url_ent.pack(fill="x", padx=20, pady=8, ipady=4)

        self._make_btn(dlg, "閉じる", dlg.destroy, bg="#334155").pack(pady=6)

    def open_doctor_dialog(self) -> None:
        tk = self.tk
        dlg = tk.Toplevel(self.root)
        dlg.title("泥濘 (Deinei) Doctor ネットワーク・接続診断")
        dlg.geometry("760x520")
        dlg.configure(bg="#0f1420")
        dlg.transient(self.root)

        top = tk.Frame(dlg, bg="#172033", padx=12, pady=8)
        top.pack(fill="x")
        tk.Label(
            top,
            text="ネットワーク・サーバー稼働診断 (Doctor)",
            bg="#172033",
            fg="#f8fafc",
            font=("Yu Gothic UI", 11, "bold"),
        ).pack(side="left")

        txt = tk.Text(
            dlg,
            bg="#090d16",
            fg="#e2e8f0",
            font=("Consolas", 10),
            relief="flat",
            padx=12,
            pady=10,
        )
        txt.pack(fill="both", expand=True, padx=12, pady=10)

        def _refresh():
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            txt.insert("end", "診断を実行中...\n")
            txt.configure(state="disabled")

            def _run():
                rep = build_doctor_report_text()
                dlg.after(0, lambda: _show(rep))

            def _show(rep: str):
                txt.configure(state="normal")
                txt.delete("1.0", "end")
                txt.insert("end", rep)
                txt.configure(state="disabled")

            threading.Thread(target=_run, daemon=True).start()

        self._make_btn(top, "再診断", _refresh, bg="#2563eb").pack(side="right")
        _refresh()

    def open_profile_dialog(self) -> None:
        tk = self.tk
        dlg = tk.Toplevel(self.root)
        dlg.title("ハードウェア・プロファイル設定 (deining_profile.bat)")
        dlg.geometry("520x340")
        dlg.configure(bg="#0f1420")
        dlg.transient(self.root)

        body = tk.Frame(dlg, bg="#161f30", padx=16, pady=16)
        body.pack(fill="both", expand=True, padx=12, pady=12)

        tk.Label(
            body,
            text=f"検出GPU: {self.profile.get('GPU_NAME', 'Unknown')} (VRAM: {self.profile.get('VRAM_GB', '?')} GB)",
            bg="#161f30",
            fg="#38bdf8",
            font=("Yu Gothic UI", 10, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        tk.Label(body, text="プロファイル名:", bg="#161f30", fg="#e2e8f0", font=("Yu Gothic UI", 9)).grid(
            row=1, column=0, sticky="w", pady=6
        )
        prof_name_var = tk.StringVar(value=str(self.profile.get("PROFILE_NAME", "16GB (High)")))
        tk.Entry(body, textvariable=prof_name_var, bg="#0f172a", fg="#f8fafc", relief="flat", width=28).grid(
            row=1, column=1, sticky="w", padx=8, ipady=3
        )

        tk.Label(
            body,
            text="MoE CPU退避数 (-ncmoe):",
            bg="#161f30",
            fg="#e2e8f0",
            font=("Yu Gothic UI", 9),
        ).grid(row=2, column=0, sticky="w", pady=6)
        ncmoe_var = tk.IntVar(value=int(self.profile.get("NCMOE", 9)))
        tk.Spinbox(
            body,
            from_=0,
            to=48,
            textvariable=ncmoe_var,
            bg="#0f172a",
            fg="#f8fafc",
            relief="flat",
            width=10,
        ).grid(row=2, column=1, sticky="w", padx=8, ipady=3)

        tk.Label(
            body,
            text="※目安: 24GB=0, 16GB=9, 12GB=16, 8GB/10GB=20",
            bg="#161f30",
            fg="#94a3b8",
            font=("Yu Gothic UI", 8),
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 8))

        has_mmproj_cur = bool(str(self.profile.get("MMPROJ_ARGS", "")).strip())
        vision_var = tk.BooleanVar(value=has_mmproj_cur)
        tk.Checkbutton(
            body,
            text="Visionエンコーダ (--mmproj) を有効化する (24GB GPU推奨)",
            variable=vision_var,
            bg="#161f30",
            fg="#e2e8f0",
            selectcolor="#0f172a",
            activebackground="#161f30",
            font=("Yu Gothic UI", 9),
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=6)

        btn_row = tk.Frame(body, bg="#161f30")
        btn_row.grid(row=5, column=0, columnspan=2, sticky="e", pady=(18, 0))

        def _save_only():
            self.profile["PROFILE_NAME"] = prof_name_var.get().strip() or "Custom"
            self.profile["NCMOE"] = int(ncmoe_var.get())
            if vision_var.get():
                mm_file = find_mmproj_model(self.root_dir)
                self.profile["MMPROJ_ARGS"] = f"--mmproj {mm_file}"
            else:
                self.profile["MMPROJ_ARGS"] = ""
            self._save_profile()
            self._emit_sys_log("[OK] deining_profile.bat を保存しました。")
            dlg.destroy()

        def _save_and_restart_llm():
            _save_only()

            def _restart_llm():
                with self.proc_lock:
                    self._emit_sys_log("[INFO] 新しいプロファイルで LLM Server (8080) を再起動しています...")
                    if self.procs.get("llm") is not None:
                        stop_popen_process(self.procs["llm"])
                        self.procs["llm"] = None
                    time.sleep(0.8)
                    self._start_llm_locked()

            threading.Thread(target=_restart_llm, daemon=True).start()

        self._make_btn(btn_row, "保存のみ", _save_only, bg="#334155").pack(side="left", padx=4)
        self._make_btn(btn_row, "保存してLLM再起動", _save_and_restart_llm, bg="#2563eb").pack(side="left", padx=4)

    def _on_close_window(self) -> None:
        self.is_shutting_down = True
        with self.proc_lock:
            self._stop_all_locked()
        cleanup_orphan_servers()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


# ==============================================================================
# CLI フォールバックモード (--cli 指定時または tkinter 非対応環境用)
# ==============================================================================
def run_cli_fallback(args: argparse.Namespace) -> None:
    if args.doctor:
        print(build_doctor_report_text())
        return

    print("========================================================")
    print(" 泥濘 (Deinei) 統合サーバーランチャー (CLI Mode)")
    print("========================================================")
    py_exe = resolve_python_executable(_ROOT_DIR)
    profile_path = os.path.join(_ROOT_DIR, "deining_profile.bat")
    profile = parse_profile_bat(open(profile_path, encoding="utf-8", errors="replace").read()) if os.path.isfile(profile_path) else parse_profile_bat("")

    if not args.no_browser:
        splash_file = os.path.join(_ROOT_DIR, "splash.html")
        if os.path.isfile(splash_file):
            webbrowser.open(f"file:///{splash_file.replace(os.sep, '/')}")

    cleanup_orphan_servers()
    ensure_jinja_template(_ROOT_DIR)
    sandbox_dir = os.path.join(_ROOT_DIR, "llama-b9873-bin-win-cuda-13.3-x64sandbox")
    llama_exe = os.path.join(sandbox_dir, "llama-server.exe")
    model_path = find_llm_model(_ROOT_DIR)
    ncmoe = str(profile.get("NCMOE", 9))

    procs: List[subprocess.Popen] = []
    try:
        if os.path.isfile(llama_exe):
            cmd_llm = [
                llama_exe, "-m", model_path, "--host", "127.0.0.1", "--no-slots", "--no-mmap",
                "-fit", "off", "-c", "16384", "-ngl", "99", "-ncmoe", ncmoe,
                "--flash-attn", "on", "--cache-reuse", "256", "-np", "1", "--parallel", "1",
                "--path", "./custom_public", "--port", "8080", "--chat-template-file", "gemma_chat_template.jinja",
            ]
            procs.append(subprocess.Popen(cmd_llm, cwd=sandbox_dir))
        procs.append(subprocess.Popen([py_exe, "-X", "utf8", "-u", "tts_server.py"], cwd=os.path.join(_ROOT_DIR, "backend", "Irodori-TTS")))
        procs.append(subprocess.Popen([py_exe, "-X", "utf8", "-u", "server.py"], cwd=os.path.join(_ROOT_DIR, "backend", "data")))
        print("[INFO] サーバーを起動しました。終了するには Ctrl+C を押してください。")
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[INFO] 全サーバーを終了しています...")
    finally:
        for p in procs:
            stop_popen_process(p)
        cleanup_orphan_servers()


_LAUNCHER_MUTEX_HANDLE = None


def _acquire_single_instance_mutex(mutex_name: str = "Local\\DeineiUnifiedLauncherMutex") -> bool:
    """Windows 名前付きミューテックスによりランチャー自身の二重起動を防止する。"""
    global _LAUNCHER_MUTEX_HANDLE
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, mutex_name)
        last_err = kernel32.GetLastError()
        if last_err == 183:  # ERROR_ALREADY_EXISTS
            if handle:
                kernel32.CloseHandle(handle)
            return False
        _LAUNCHER_MUTEX_HANDLE = handle
        return True
    except Exception:
        return True


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="泥濘 (Deinei) 統合サーバーランチャー")
    parser.add_argument(
        "--mode",
        choices=["local", "lan", "tailscale"],
        default=None,
        help="ネットワーク公開モード (local / lan / tailscale)",
    )
    parser.add_argument("--lan", action="store_true", help="LAN公開モードで起動 (--mode lan の短縮形)")
    parser.add_argument("--tailscale", action="store_true", help="Tailscale公開モードで起動 (--mode tailscale の短縮形)")
    parser.add_argument("--https", action="store_true", help="Tailscale HTTPS モードを有効化")
    parser.add_argument("--port", type=int, default=8090, help="Gateway ポート番号 (既定: 8090)")
    parser.add_argument("--token", type=str, default=None, help="Gateway 固定アクセストークン")
    parser.add_argument("--doctor", action="store_true", help="Doctor 接続診断を開く")
    parser.add_argument("--no-browser", action="store_true", help="起動時の splash.html 自動表示を行わない")
    parser.add_argument("--no-autostart", action="store_true", help="起動時のサーバー自動起動を行わない")
    parser.add_argument("--cli", action="store_true", help="GUIを使わずCLIモードで実行する")
    args = parser.parse_args(argv)
    if args.lan and not args.mode:
        args.mode = "lan"
    if args.tailscale and not args.mode:
        args.mode = "tailscale"
    return args


# ==============================================================================
# PyWebView モダンダッシュボード ブリッジ & ランチャー本体
# ==============================================================================
class LauncherApiBridge:
    """pywebview の JavaScript 層 (launcher_ui.html) と通信するスレッドセーフな API ブリッジ。"""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.root_dir = _ROOT_DIR
        self.py_exe = resolve_python_executable(self.root_dir)
        self.profile_path = os.path.join(self.root_dir, "deining_profile.bat")
        self.profile = self._load_profile()

        self.procs: Dict[str, Optional[subprocess.Popen]] = {
            "llm": None,
            "flask": None,
            "tts": None,
            "gw": None,
        }
        self.proc_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.log_queue: queue.Queue[Tuple[str, str]] = queue.Queue()
        self.is_shutting_down = False

        init_mode = args.mode
        if not init_mode:
            env_gw = os.environ.get("DEINEI_GATEWAY_MODE", "").strip().lower()
            init_mode = env_gw if env_gw in ("local", "lan", "tailscale") else (
                "lan" if int(self.profile.get("ALLOW_LAN", 0)) == 1 else "local"
            )
        self.gateway_mode = init_mode
        self.tailscale_https = bool(args.https)
        self.gateway_token = args.token
        self.gateway_port = int(args.port)
        self.gateway_url = "http://127.0.0.1:8080 (ローカル専用)"

        self.current_rag_section: Optional[str] = None
        self.rag_rows: List[Dict[str, Any]] = []
        self.rag_summary = "クエリ: (未実行) | コレクション総数: - | 採用: Episode 0件 / Fact 0件"
        self.rag_version = 0

        self.log_seq = 0
        self.log_buffer: List[Dict[str, Any]] = []

        self.telemetry: Dict[str, Any] = {
            "profile_summary": self._format_profile_summary(),
            "tps": "--.-- t/s",
            "prompt_eval": "Prompt: -- ms (-- tok)",
            "ctx_text": "0 / 16,384 tok (0.0%)",
            "ctx_pct": 0.0,
            "kv_reuse": "待機中 (初回発話後に計測)",
            "kv_pct": 0.0,
            "kv_mismatch": "食い違い位置: -",
            "search_toggle": "ON",
            "search_query": "(未実行)",
            "tts_state": "待機中",
            "tts_text": "(まだ読み上げられたセリフはありません)",
            "gateway_url": self.gateway_url,
        }

        self.badges: Dict[str, Dict[str, str]] = {
            "llm": {"state": "offline", "text": "LLM (8080): OFFLINE"},
            "flask": {"state": "offline", "text": "Data/RAG (5000): OFFLINE"},
            "tts": {"state": "offline", "text": "TTS (5001): OFFLINE"},
            "gw": {"state": "disabled", "text": "Gateway (8090): DISABLED"},
        }
        self._open_doctor_once = bool(args.doctor)

    def _load_profile(self) -> Dict[str, Any]:
        if os.path.isfile(self.profile_path):
            try:
                with open(self.profile_path, "r", encoding="utf-8", errors="replace") as f:
                    return parse_profile_bat(f.read())
            except Exception:
                pass
        return parse_profile_bat("")

    def _save_profile_to_disk(self) -> None:
        content = serialize_profile_bat(self.profile)
        with open(self.profile_path, "wb") as f:
            f.write(content.encode("utf-8"))
        with self.state_lock:
            self.telemetry["profile_summary"] = self._format_profile_summary()

    def _format_profile_summary(self) -> str:
        p_name = self.profile.get("PROFILE_NAME", "16GB (High)")
        ncmoe = self.profile.get("NCMOE", 9)
        vision = "ON (GPU)" if str(self.profile.get("MMPROJ_ARGS", "")).strip() else "OFF (FlashAttn優先)"
        return f"プロファイル: {p_name}  |  -ncmoe: {ncmoe}  |  Vision: {vision}"

    def start_background_workers(self) -> None:
        threading.Thread(target=self._log_consumer_loop, daemon=True).start()
        threading.Thread(target=self._health_check_loop, daemon=True).start()
        open_browser = not self.args.no_browser and not self.args.doctor
        if not self.args.no_autostart:
            threading.Thread(
                target=self._start_all_servers_worker,
                kwargs={"force_restart": True, "open_browser_after_cleanup": open_browser},
                daemon=True,
            ).start()
        elif open_browser:
            self.open_splash_in_browser()

    def _emit_sys_log(self, msg: str) -> None:
        self.log_queue.put(("sys", msg))

    def _log_consumer_loop(self) -> None:
        while not self.is_shutting_down:
            try:
                source, raw_line = self.log_queue.get(timeout=0.15)
                self._process_single_log(source, raw_line)
            except queue.Empty:
                pass
            except Exception:
                pass

    def _process_single_log(self, source: str, raw_line: str) -> None:
        clean_line = strip_ansi(raw_line).rstrip("\r\n")
        if not clean_line:
            return

        with self.state_lock:
            if source == "llm":
                info = parse_llm_log_line(clean_line)
                if "eval_tps" in info:
                    self.telemetry["tps"] = (
                        f"{info['eval_tps']:.2f} t/s ({info['eval_tokens']} tok / {info['eval_ms']:.0f} ms)"
                    )
                if "prompt_eval_ms" in info:
                    self.telemetry["prompt_eval"] = (
                        f"Prompt: {info['prompt_tps']:.1f} t/s ({info['prompt_eval_ms']:.0f} ms / {info['prompt_eval_tokens']} tok)"
                    )
                if "n_tokens" in info:
                    nt = info["n_tokens"]
                    pct = (nt / 16384.0) * 100.0
                    self.telemetry["ctx_text"] = f"{nt:,} / 16,384 tok ({pct:.1f}%)"
                    self.telemetry["ctx_pct"] = round(pct, 1)

            elif source == "flask":
                info = parse_flask_log_line(clean_line, self.current_rag_section)
                if "rag_section" in info:
                    self.current_rag_section = info["rag_section"]

                if "token_cache" in info:
                    tc = info["token_cache"]
                    if tc.get("is_initial"):
                        self.telemetry["kv_reuse"] = f"初回ターン (総数: {tc['curr_tokens']:,} tok)"
                        self.telemetry["kv_pct"] = 0.0
                        self.telemetry["kv_mismatch"] = "食い違い位置: - (初回)"
                    else:
                        self.telemetry["kv_reuse"] = (
                            f"{tc['reused_tokens']:,} tok 再利用 ({tc['reuse_pct']:.1f}%) "
                            f"[前回: {tc['prev_tokens']:,} -> 今回: {tc['curr_tokens']:,} tok]"
                        )
                        self.telemetry["kv_pct"] = float(tc["reuse_pct"])

                if "token_cache_mismatch_pos" in info:
                    pos = info["token_cache_mismatch_pos"]
                    self.telemetry["kv_mismatch"] = f"食い違い位置: {pos:,} トークン目"

                if "search_enabled" in info:
                    self.telemetry["search_toggle"] = "ON" if info["search_enabled"] else "OFF"

                if "search_query" in info:
                    q = info["search_query"]
                    cnt = info.get("search_result_count")
                    self.telemetry["search_query"] = f"'{q}' ({cnt}件取得)" if cnt is not None else f"'{q}'"

                if "rag_query_header" in info:
                    qh = info["rag_query_header"]
                    self.rag_rows.clear()
                    self.rag_summary = (
                        f"クエリ: 「{qh['query'][:28]}」 | コレクション総数: {qh['collection_count']}件"
                    )
                    self.rag_version += 1

                if "rag_candidate" in info:
                    cand = info["rag_candidate"]
                    if cand["adopted"]:
                        tag = "adopted"
                    elif cand["reason"] == "世界線外":
                        tag = "worldline_out"
                    else:
                        tag = "rejected"
                    self.rag_rows.append(
                        {
                            "status": cand["status_label"],
                            "category": cand["category"],
                            "similarity": float(cand["similarity"]),
                            "distance": float(cand["distance"]),
                            "key": cand["key"],
                            "content": cand["content"],
                            "adopted": bool(cand["adopted"]),
                            "tag": tag,
                        }
                    )
                    self.rag_version += 1

                if "rag_final_counts" in info:
                    fc = info["rag_final_counts"]
                    base = self.rag_summary.split(" | 採用:")[0]
                    self.rag_summary = f"{base} | 採用: Episode {fc['episode_count']}件 / Fact {fc['fact_count']}件"
                    self.rag_version += 1

                if "rag_top6_item" in info:
                    t6 = info["rag_top6_item"]
                    rank = t6["rank"]
                    cat = t6["category"]
                    sim = float(t6["similarity"])
                    for row in self.rag_rows:
                        if row["category"] == cat and abs(row["similarity"] - sim) < 0.002 and "[OK]" in row["status"]:
                            row["status"] = f"[OK] 採用 #{rank}"
                            row["tag"] = "adopted_top"
                            break
                    self.rag_version += 1

            elif source == "tts":
                info = parse_tts_log_line(clean_line)
                if info.get("tts_status") == "SYNTHESIZING":
                    self.telemetry["tts_state"] = "音声合成中 (SYNTHESIZING)"
                elif info.get("tts_completed"):
                    self.telemetry["tts_state"] = "合成完了 (ONLINE / 待機中)"
                if "clean_text" in info:
                    ctext = info["clean_text"]
                    ccount = info.get("clean_char_count", len(ctext))
                    self.telemetry["tts_text"] = f"({ccount}文字) {ctext}"

            elif source == "gw":
                info = parse_gateway_log_line(clean_line)
                if "gateway_url" in info:
                    self.gateway_url = info["gateway_url"]
                    self.telemetry["gateway_url"] = self.gateway_url

            is_noise = is_noise_log_line(source, clean_line)
            is_divider = set(clean_line.strip()) <= {"=", "-"} and len(clean_line.strip()) >= 15
            is_err = "[ERROR]" in clean_line or "Traceback" in clean_line or "Exception:" in clean_line
            badge_map = {"llm": "LLM", "flask": "RAG", "tts": "TTS", "gw": "GW", "sys": "SYS"}

            self.log_seq += 1
            self.log_buffer.append(
                {
                    "seq": self.log_seq,
                    "source": source,
                    "ts": datetime.now().strftime("%H:%M:%S"),
                    "badge": badge_map.get(source, "LOG"),
                    "line": clean_line,
                    "is_noise": is_noise,
                    "is_divider": is_divider,
                    "is_error": is_err,
                }
            )
            if len(self.log_buffer) > 2500:
                self.log_buffer = self.log_buffer[-2000:]

    def _health_check_loop(self) -> None:
        while not self.is_shutting_down:
            llm_ok, llm_msg = check_http_health("127.0.0.1", 8080, "/health")
            flask_ok, flask_msg = check_http_health("127.0.0.1", 5000, "/health")
            tts_ok, tts_msg = check_http_health("127.0.0.1", 5001, "/health")

            if flask_ok:
                try:
                    conn = http.client.HTTPConnection("127.0.0.1", 5000, timeout=0.6)
                    conn.request("GET", "/api/search/status")
                    r = conn.getresponse()
                    if r.status == 200:
                        data = json.loads(r.read().decode("utf-8", errors="replace"))
                        en = bool(data.get("search_enabled", True))
                        with self.state_lock:
                            self.telemetry["search_toggle"] = "ON" if en else "OFF"
                    conn.close()
                except Exception:
                    pass

            # state_lock 保持中に外部サブプロセスを呼ばないよう、ロック外で Tailscale 状態を取得
            cur_mode = self.gateway_mode
            ts_info = get_tailscale_info() if cur_mode == "tailscale" else None

            def _make_badge(key: str, prefix: str, ok: bool, msg: str) -> Dict[str, str]:
                proc = self.procs.get(key)
                running = proc is not None and proc.poll() is None
                if ok:
                    return {"state": "online", "text": f"{prefix}: ONLINE"}
                if "ERROR" in msg:
                    return {"state": "offline", "text": f"{prefix}: {msg}"}
                if running or "LOADING" in msg:
                    return {"state": "loading", "text": f"{prefix}: LOADING"}
                return {"state": "offline", "text": f"{prefix}: OFFLINE"}

            with self.state_lock:
                self.badges["llm"] = _make_badge("llm", "LLM (8080)", llm_ok, llm_msg)
                self.badges["flask"] = _make_badge("flask", "Data/RAG (5000)", flask_ok, flask_msg)
                self.badges["tts"] = _make_badge("tts", "TTS (5001)", tts_ok, tts_msg)

                gw_proc = self.procs.get("gw")
                if self.gateway_mode == "local":
                    self.badges["gw"] = {"state": "disabled", "text": "Gateway (8090): DISABLED"}
                elif self.gateway_mode == "tailscale":
                    info_ts = ts_info or {}
                    if not info_ts.get("installed"):
                        self.badges["gw"] = {"state": "offline", "text": "Gateway: TAILSCALE 未導入"}
                    elif not info_ts.get("running"):
                        self.badges["gw"] = {"state": "loading", "text": "Gateway: TAILSCALE 認証待ち"}
                    elif gw_proc is not None and gw_proc.poll() is None:
                        self.badges["gw"] = {"state": "online", "text": "Gateway (8090): TAILSCALE ONLINE"}
                    else:
                        self.badges["gw"] = {"state": "loading", "text": "Gateway (8090): TAILSCALE STARTING"}
                elif gw_proc is not None and gw_proc.poll() is None:
                    self.badges["gw"] = {
                        "state": "online",
                        "text": f"Gateway (8090): {self.gateway_mode.upper()} ONLINE",
                    }
                else:
                    self.badges["gw"] = {
                        "state": "loading",
                        "text": f"Gateway (8090): {self.gateway_mode.upper()} STARTING",
                    }
            time.sleep(2.0)

    def _spawn_process(self, key: str, cmd: List[str], cwd: str) -> None:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        flags = CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                creationflags=flags,
            )
            self.procs[key] = proc
            threading.Thread(target=self._reader_thread, args=(key, proc), daemon=True).start()
        except Exception as e:
            self._emit_sys_log(f"[ERROR] {key} プロセスの起動に失敗しました: {e}")

    def _reader_thread(self, key: str, proc: subprocess.Popen) -> None:
        if proc.stdout is None:
            return
        try:
            for raw_bytes in iter(proc.stdout.readline, b""):
                if not raw_bytes:
                    break
                line = raw_bytes.decode("utf-8", errors="replace")
                self.log_queue.put((key, line))
        except Exception:
            pass
        finally:
            rc = proc.poll()
            if not self.is_shutting_down:
                if rc is not None and rc != 0:
                    self.log_queue.put((key, f"[ERROR] プロセス '{key}' が異常終了しました (exit code: {rc})\n"))
                self._emit_sys_log(f"[INFO] プロセス '{key}' が終了しました (exit code: {rc})")

    def _start_llm_locked(self) -> None:
        ensure_jinja_template(self.root_dir)
        sandbox_dir = os.path.join(self.root_dir, "llama-b9873-bin-win-cuda-13.3-x64sandbox")
        llama_exe = os.path.join(sandbox_dir, "llama-server.exe")
        if not os.path.isfile(llama_exe):
            self._emit_sys_log(f"[ERROR] llama-server.exe が見つかりません: {llama_exe}")
            return
        model_path = find_llm_model(self.root_dir)
        ncmoe = str(self.profile.get("NCMOE", 9))
        mmproj_raw = str(self.profile.get("MMPROJ_ARGS", "")).strip()
        cmd = [
            llama_exe,
            "-m",
            model_path,
            "--host",
            "127.0.0.1",
            "--no-slots",
            "--no-mmap",
            "-fit",
            "off",
            "-c",
            "16384",
            "-ngl",
            "99",
            "-ncmoe",
            ncmoe,
            "--flash-attn",
            "on",
            "--cache-reuse",
            "256",
            "-np",
            "1",
            "--parallel",
            "1",
            "--reasoning-format",
            "deepseek",
            "--reasoning",
            "on",
            "--chat-template-file",
            "gemma_chat_template.jinja",
            "--path",
            "./custom_public",
            "--port",
            "8080",
        ]
        if mmproj_raw:
            cmd.extend(mmproj_raw.split())
        self._emit_sys_log(f"[INFO] LLM Server (8080) を起動しています... (-ncmoe {ncmoe})")
        self._spawn_process("llm", cmd, cwd=sandbox_dir)

    def _start_tts_locked(self) -> None:
        tts_dir = os.path.join(self.root_dir, "backend", "Irodori-TTS")
        tts_script = os.path.join(tts_dir, "tts_server.py")
        if not os.path.isfile(tts_script):
            self._emit_sys_log(f"[ERROR] tts_server.py が見つかりません: {tts_script}")
            return
        cmd = [self.py_exe, "-X", "utf8", "-u", "tts_server.py"]
        self._emit_sys_log("[INFO] Irodori-TTS Server (5001) を起動しています...")
        self._spawn_process("tts", cmd, cwd=tts_dir)

    def _start_flask_locked(self) -> None:
        data_dir = os.path.join(self.root_dir, "backend", "data")
        flask_script = os.path.join(data_dir, "server.py")
        if not os.path.isfile(flask_script):
            self._emit_sys_log(f"[ERROR] server.py が見つかりません: {flask_script}")
            return
        cmd = [self.py_exe, "-X", "utf8", "-u", "server.py"]
        self._emit_sys_log("[INFO] Data / RAG Server (5000) を起動しています...")
        self._spawn_process("flask", cmd, cwd=data_dir)

    def _start_gateway_locked(self) -> None:
        if self.procs.get("gw") is not None:
            stop_popen_process(self.procs["gw"])
            self.procs["gw"] = None

        if self.gateway_mode not in ("lan", "tailscale"):
            with self.state_lock:
                self.gateway_url = "http://127.0.0.1:8080 (ローカル専用)"
                self.telemetry["gateway_url"] = self.gateway_url
            self._emit_sys_log("[INFO] 公開モード: ローカルのみ (127.0.0.1:8080)")
            return

        data_dir = os.path.join(self.root_dir, "backend", "data")
        gw_script = os.path.join(data_dir, "deinei_net.py")
        if not os.path.isfile(gw_script):
            self._emit_sys_log(f"[ERROR] deinei_net.py が見つかりません: {gw_script}")
            return

        cmd = [self.py_exe, "-X", "utf8", "-u", gw_script, "serve", "--no-qr"]
        if self.gateway_mode == "lan":
            cmd.append("--lan")
        elif self.gateway_mode == "tailscale":
            cmd.append("--tailscale")
            if self.tailscale_https:
                cmd.append("--https")
        if self.gateway_port:
            cmd.extend(["--port", str(self.gateway_port)])
        if self.gateway_token:
            cmd.extend(["--token", str(self.gateway_token)])

        # 泥濘自身の Gateway 残存プロセスがあれば事前に解放
        cleaned = cleanup_orphan_servers(target_ports=[self.gateway_port or 8090])
        for c_msg in cleaned:
            self._emit_sys_log(f"[INFO] 残存 Gateway クリーンアップ: {c_msg}")

        self._emit_sys_log(f"[INFO] Remote Gateway ({self.gateway_port}) を [{self.gateway_mode.upper()}] モードで起動しています...")
        self._spawn_process("gw", cmd, cwd=data_dir)

    def _stop_all_locked(self) -> None:
        for key in ("gw", "flask", "tts", "llm"):
            proc = self.procs.get(key)
            if proc is not None:
                stop_popen_process(proc)
                self.procs[key] = None

    def _start_all_servers_worker(
        self, force_restart: bool = False, open_browser_after_cleanup: bool = False
    ) -> None:
        with self.proc_lock:
            has_own_procs = any(self.procs.get(k) is not None for k in ("llm", "flask", "tts"))

            if force_restart or not has_own_procs:
                if force_restart and has_own_procs:
                    self._emit_sys_log("[INFO] 既存のサーバープロセスを停止しています...")
                else:
                    self._emit_sys_log("[INFO] 起動時初期化: 残存孤児サーバープロセスのクリーンアップを実行しています...")
                self._stop_all_locked()
                cleaned = cleanup_orphan_servers()
                for c_msg in cleaned:
                    self._emit_sys_log(f"[INFO] 残存プロセスクリーンアップ: {c_msg}")
                time.sleep(0.8)

                if open_browser_after_cleanup:
                    self.open_splash_in_browser()

                need_llm, need_flask, need_tts = True, True, True
            else:
                llm_ok, llm_msg = check_http_health("127.0.0.1", 8080, "/health")
                flask_ok, flask_msg = check_http_health("127.0.0.1", 5000, "/health")
                tts_ok, tts_msg = check_http_health("127.0.0.1", 5001, "/health")

                llm_alive = llm_ok or ("LOADING" in llm_msg)
                flask_alive = flask_ok or ("LOADING" in flask_msg)
                tts_alive = tts_ok or ("LOADING" in tts_msg)

                if llm_alive and flask_alive and tts_alive:
                    self._emit_sys_log("[OK] 3つのバックエンドサーバー (8080, 5000, 5001) は既に稼働またはロード中です。")
                    self._start_gateway_locked()
                    return

                need_llm = not llm_alive
                need_flask = not flask_alive
                need_tts = not tts_alive
                orphan_ports: List[int] = []
                if need_llm:
                    if self.procs.get("llm") is not None:
                        stop_popen_process(self.procs["llm"])
                        self.procs["llm"] = None
                    orphan_ports.append(8080)
                if need_flask:
                    if self.procs.get("flask") is not None:
                        stop_popen_process(self.procs["flask"])
                        self.procs["flask"] = None
                    orphan_ports.append(5000)
                if need_tts:
                    if self.procs.get("tts") is not None:
                        stop_popen_process(self.procs["tts"])
                        self.procs["tts"] = None
                    orphan_ports.append(5001)
                if self.gateway_mode in ("lan", "tailscale"):
                    if self.procs.get("gw") is not None:
                        stop_popen_process(self.procs["gw"])
                        self.procs["gw"] = None
                    orphan_ports.append(self.gateway_port or 8090)

                cleaned = cleanup_orphan_servers(target_ports=orphan_ports)
                for c_msg in cleaned:
                    self._emit_sys_log(f"[INFO] 残存プロセスクリーンアップ: {c_msg}")
                if cleaned:
                    time.sleep(0.8)

            self._emit_sys_log(f"[INFO] バックエンド Python 実行環境: {self.py_exe}")
            # 1. Flask Data Server (5000) を最優先で起動し、Ruri埋め込みモデルのロード完了を待機
            #    (LLM --no-mmap 約17GB / TTS 約8GB との同時コミットチャージ衝突による os error 1455 を回避)
            if need_flask:
                self._start_flask_locked()
                for _ in range(40):
                    time.sleep(0.5)
                    f_proc = self.procs.get("flask")
                    if f_proc is not None and f_proc.poll() is not None:
                        break
                    f_ok, f_msg = check_http_health("127.0.0.1", 5000, "/health")
                    if f_ok or "ERROR" in f_msg:
                        break
            if need_llm:
                self._start_llm_locked()
                time.sleep(1.0)
            if need_tts:
                self._start_tts_locked()
                time.sleep(1.0)
            self._start_gateway_locked()

    def open_splash_in_browser(self) -> None:
        splash_file = os.path.join(self.root_dir, "splash.html")
        if os.path.isfile(splash_file):
            webbrowser.open(f"file:///{splash_file.replace(os.sep, '/')}")
        else:
            webbrowser.open("http://127.0.0.1:8080")

    # --- JavaScript から直接呼ばれる公開 API メソッド ---
    def get_initial_state(self) -> Dict[str, Any]:
        with self.state_lock:
            return {
                "gateway_mode": self.gateway_mode,
                "tailscale_https": self.tailscale_https,
                "profile_summary": self.telemetry["profile_summary"],
            }

    def poll_updates(self, last_seq: int = 0, client_rag_version: int = -1) -> Dict[str, Any]:
        with self.state_lock:
            new_logs = [item for item in self.log_buffer if item["seq"] > last_seq]
            rag_updated = self.rag_version != client_rag_version
            open_doc = self._open_doctor_once
            self._open_doctor_once = False
            return {
                "last_seq": self.log_seq,
                "badges": dict(self.badges),
                "telemetry": dict(self.telemetry),
                "logs": new_logs,
                "rag_updated": rag_updated,
                "rag_version": self.rag_version,
                "rag_summary": self.rag_summary,
                "rag_rows": list(self.rag_rows) if rag_updated else [],
                "open_doctor_once": open_doc,
            }

    def open_webui(self) -> bool:
        webbrowser.open("http://127.0.0.1:8080")
        return True

    def start_or_restart_servers(self) -> bool:
        threading.Thread(target=self._start_all_servers_worker, kwargs={"force_restart": True}, daemon=True).start()
        return True

    def stop_servers(self) -> bool:
        def _worker():
            with self.proc_lock:
                self._emit_sys_log("[INFO] 全サーバーを停止しています...")
                self._stop_all_locked()
                cleanup_orphan_servers()
                self._emit_sys_log("[OK] 全サーバーを停止しました。")

        threading.Thread(target=_worker, daemon=True).start()
        return True

    def set_network_mode(self, mode: str, https_enabled: bool = False) -> Dict[str, Any]:
        self.gateway_mode = mode if mode in ("local", "lan", "tailscale") else "local"
        self.tailscale_https = bool(https_enabled)
        self.profile["ALLOW_LAN"] = 1 if self.gateway_mode == "lan" else 0
        self._save_profile_to_disk()

        def _worker():
            with self.proc_lock:
                self._start_gateway_locked()

        threading.Thread(target=_worker, daemon=True).start()
        ts_info = get_tailscale_info() if self.gateway_mode == "tailscale" else None
        return {"ok": True, "mode": self.gateway_mode, "tailscale": ts_info}

    def get_tailscale_status(self) -> Dict[str, Any]:
        return get_tailscale_info()

    def install_tailscale(self) -> Dict[str, Any]:
        def _bg():
            self._emit_sys_log("[INFO] Tailscale インストーラーのダウンロードを開始します...")
            def _cb(msg: str):
                self._emit_sys_log(f"[INFO] {msg}")

            ok, msg = download_and_install_tailscale(download_callback=_cb)
            if ok:
                self._emit_sys_log(f"[OK] {msg}")
                if self.gateway_mode == "tailscale":
                    with self.proc_lock:
                        self._start_gateway_locked()
            else:
                self._emit_sys_log(f"[WARN] {msg}")

        threading.Thread(target=_bg, daemon=True).start()
        return {"ok": True, "message": "Tailscale インストール処理をバックグラウンドで開始しました。"}

    def login_tailscale(self) -> Dict[str, Any]:
        self._emit_sys_log("[INFO] Tailscale ログイン画面をブラウザで起動しています...")
        ok, msg = trigger_tailscale_login()
        if ok:
            self._emit_sys_log(f"[OK] {msg}")
        else:
            self._emit_sys_log(f"[WARN] {msg}")
        return {"ok": ok, "message": msg}

    def get_qr_svg(self) -> Dict[str, Any]:
        url = self.gateway_url.strip()
        if not url.startswith("http") or "ローカル専用" in url:
            return {
                "ok": False,
                "message": "現在ローカル専用モードです。スマートフォン接続には「LAN 公開」または「Tailscale 公開」を選択してください。",
            }
        try:
            import io
            import segno

            qr = segno.make(url, error="m")
            buf = io.BytesIO()
            qr.save(buf, kind="svg", scale=5, border=2, xmldecl=False)
            svg_str = buf.getvalue().decode("utf-8")
            return {"ok": True, "url": url, "svg": svg_str}
        except Exception as e:
            return {"ok": False, "message": f"QRコード生成エラー: {e}"}

    def run_doctor(self) -> str:
        return build_doctor_report_text()

    def get_profile(self) -> Dict[str, Any]:
        return dict(self.profile)

    def save_profile(self, preset: str, ncmoe: int, vision_enabled: bool, restart_after: bool = False) -> bool:
        self.profile["PROFILE_NAME"] = preset
        self.profile["NCMOE"] = max(0, min(48, int(ncmoe)))
        self.profile["MMPROJ_ARGS"] = "--mmproj mmproj-BF16.gguf" if vision_enabled else ""
        self._save_profile_to_disk()
        self._emit_sys_log("[OK] deining_profile.bat を保存しました。")
        if restart_after:
            self.start_or_restart_servers()
        return True

    def clear_rag_history(self) -> bool:
        with self.state_lock:
            self.rag_rows.clear()
            self.rag_version += 1
        return True

    def shutdown(self) -> None:
        if self.is_shutting_down:
            return
        self.is_shutting_down = True
        with self.proc_lock:
            self._stop_all_locked()
        cleanup_orphan_servers()


def run_pywebview_launcher(args: argparse.Namespace) -> None:
    import webview

    html_path = os.path.join(_CURRENT_DIR, "launcher_ui.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    bridge = LauncherApiBridge(args)
    bridge.start_background_workers()

    window = webview.create_window(
        title="泥濘 (Deinei) 統合サーバーランチャー & テレメトリコンソール",
        html=html_content,
        js_api=bridge,
        width=1260,
        height=880,
        min_size=(1020, 720),
        background_color="#0a0e1a",
    )
    window.events.closed += bridge.shutdown
    webview_storage = os.path.join(_CACHE_DIR, "pywebview")
    os.makedirs(webview_storage, exist_ok=True)
    try:
        webview.start(debug=False, storage_path=webview_storage)
    finally:
        bridge.shutdown()


def main() -> None:
    args = parse_args()
    if args.cli:
        run_cli_fallback(args)
        return

    if not args.doctor and not _acquire_single_instance_mutex():
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(
                "泥濘 (Deinei) ランチャー",
                "泥濘サーバーランチャーは既に起動しています。\n公開モード (LAN / Tailscale) の切り替えや再起動は、起動中のランチャーウィンドウから行えます。",
            )
            root.destroy()
        except Exception:
            print("[WARN] 泥濘サーバーランチャーは既に起動しています。")
        return

    try:
        import webview  # noqa: F401

        run_pywebview_launcher(args)
        return
    except Exception as e:
        print(f"[WARN] pywebview が利用できないため Tkinter フォールバックを起動します ({e})")

    try:
        import tkinter as tk  # noqa: F401
    except Exception as e:
        print(f"[WARN] Tkinter GUI が利用できないため CLI モードへフォールバックします ({e})")
        run_cli_fallback(args)
        return

    app = DeineiLauncherGUI(args)
    app.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        err_msg = traceback.format_exc()
        try:
            crash_log = os.path.join(_ROOT_DIR, "launcher_crash.log")
            with open(crash_log, "w", encoding="utf-8") as f:
                f.write(err_msg)
        except Exception:
            pass
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "泥濘 (Deinei) ランチャー 起動エラー",
                f"ランチャーの起動中に予期せぬエラーが発生しました:\n\n{e}\n\n詳細は launcher_crash.log を確認してください。",
            )
            root.destroy()
        except Exception:
            pass
        raise


