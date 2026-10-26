"""
泥濘 (Deinei) ネットワーク探索・診断モジュール (純粋関数)
--------------------------------------------------------------------------------
LAN IPv4 アドレスの自動検出、仮想アダプタ除外、Windows ネットワークプロファイル診断、
Tailscale CLI 連携・状態取得を担当。
外部フレームワークに依存せず Python 標準ライブラリのみで構成。
"""

import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple


# 除外対象の仮想アダプタ名・パターン (大文字小文字無視)
VIRTUAL_ADAPTER_PATTERN = re.compile(
    r"vEthernet|Virtual|WSL|Docker|Tailscale|ZeroTier|Hyper-V|VMware|VirtualBox|Loopback|Bluetooth|TAP|TUN",
    re.IGNORECASE,
)


def is_excluded_ip(ip_str: str) -> bool:
    """
    除外すべきIPアドレス (ループバック、リンクローカル、CGNAT、マルチキャスト等) かどうか判定。
    """
    try:
        ip = ipaddress.ip_address(ip_str.strip())
        if ip.version != 4:
            return True
        if ip.is_loopback:  # 127.0.0.0/8
            return True
        if ip.is_link_local:  # 169.254.0.0/16 (APIPA)
            return True
        if ip.is_multicast:  # 224.0.0.0/4
            return True
        if ip.is_reserved or ip.is_unspecified:
            return True
        # Tailscale 等の CGNAT (100.64.0.0/10) は LAN IP としては除外
        cgnat = ipaddress.ip_network("100.64.0.0/10")
        if ip in cgnat:
            return True
        return False
    except ValueError:
        return True


def is_private_ip(ip_str: str) -> bool:
    """
    RFC 1918 プライベート IPv4 (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16) かどうか判定。
    """
    try:
        ip = ipaddress.ip_address(ip_str.strip())
        return ip.version == 4 and ip.is_private and not is_excluded_ip(ip_str)
    except ValueError:
        return False


def get_ip_priority(ip_str: str) -> int:
    """
    LAN IP の推奨優先度を返す (数値が小さいほど優先)。
    192.168.x.x (家庭用Wi-Fi) -> 1
    10.x.x.x (社内/ラボLAN)   -> 2
    172.16-31.x.x             -> 3
    その他                    -> 4
    """
    if ip_str.startswith("192.168."):
        return 1
    if ip_str.startswith("10."):
        return 2
    if re.match(r"^172\.(1[6-9]|2[0-9]|3[0-1])\.", ip_str):
        return 3
    return 4


def _get_lan_candidates_powershell() -> List[Dict[str, any]]:
    """
    PowerShell の Get-NetIPAddress を使用して IPv4 一覧を取得する (Windows用)。
    """
    candidates = []
    ps_cmd = (
        "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
        "Select-Object IPAddress, InterfaceAlias | "
        "ConvertTo-Json -Compress"
    )
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            # 単一オブジェクトの場合はリスト化
            if isinstance(data, dict):
                data = [data]
            for item in data:
                ip = str(item.get("IPAddress", "")).strip()
                alias = str(item.get("InterfaceAlias", "")).strip()
                if not ip or is_excluded_ip(ip):
                    continue
                if VIRTUAL_ADAPTER_PATTERN.search(alias):
                    continue
                if is_private_ip(ip):
                    candidates.append({
                        "ip": ip,
                        "interface": alias,
                        "priority": get_ip_priority(ip),
                    })
    except Exception:
        pass
    return candidates


def _get_lan_candidates_socket_fallback() -> List[Dict[str, any]]:
    """
    PowerShell が利用できない、または候補が取得できなかった場合のソケットベースフォールバック。
    外部へ通信は送出せず、ローカルルーティングテーブルから外向きIPを特定する。
    """
    candidates = []
    # 1. ルーティングソケットによるプライマリIP特定
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # 10.255.255.255 はダミー (パケットは送信されない)
            s.connect(("10.255.255.255", 1))
            primary_ip = s.getsockname()[0]
            if is_private_ip(primary_ip) and not is_excluded_ip(primary_ip):
                candidates.append({
                    "ip": primary_ip,
                    "interface": "Primary Interface",
                    "priority": get_ip_priority(primary_ip),
                })
    except Exception:
        pass

    # 2. ホスト名解決によるIP列挙
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if is_private_ip(ip) and not is_excluded_ip(ip):
                if not any(c["ip"] == ip for c in candidates):
                    candidates.append({
                        "ip": ip,
                        "interface": "Hostname Resolution",
                        "priority": get_ip_priority(ip),
                    })
    except Exception:
        pass

    return candidates


def get_lan_ip_candidates() -> List[Dict[str, any]]:
    """
    仮想アダプタや無効なIPを除外した LAN IPv4 アドレス候補一覧を取得し、優先度順にソートして返す。
    各要素: { "ip": str, "interface": str, "priority": int }
    """
    candidates = _get_lan_candidates_powershell()
    if not candidates:
        candidates = _get_lan_candidates_socket_fallback()

    # 重複除去 (IP単位)
    unique_candidates = []
    seen = set()
    for c in candidates:
        if c["ip"] not in seen:
            seen.add(c["ip"])
            unique_candidates.append(c)

    # 優先度順、同順位なら IP 文字列順でソート
    unique_candidates.sort(key=lambda x: (x["priority"], x["ip"]))
    return unique_candidates


def get_best_lan_ip() -> Optional[str]:
    """
    最も適した LAN IPv4 アドレスを1件返す。候補が存在しない場合は None。
    """
    candidates = get_lan_ip_candidates()
    if candidates:
        return candidates[0]["ip"]
    return None


def get_network_profiles() -> List[Dict[str, str]]:
    """
    Windows のネットワーク接続プロファイル情報を取得する (Private / Public 診断用)。
    戻り値: [{ "name": str, "interface": str, "category": str }]
    """
    profiles = []
    ps_cmd = (
        "Get-NetConnectionProfile -ErrorAction SilentlyContinue | "
        "Select-Object Name, InterfaceAlias, NetworkCategory | "
        "ConvertTo-Json -Compress"
    )
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            if isinstance(data, dict):
                data = [data]
            for item in data:
                cat_val = item.get("NetworkCategory", "")
                cat_str = "Private" if cat_val == 1 or cat_val == "Private" else ("Public" if cat_val == 0 or cat_val == "Public" else str(cat_val))
                profiles.append({
                    "name": str(item.get("Name", "")).strip(),
                    "interface": str(item.get("InterfaceAlias", "")).strip(),
                    "category": cat_str,
                })
    except Exception:
        pass
    return profiles


def find_tailscale_binary() -> Optional[str]:
    """
    Tailscale CLI バイナリのパスを検出する。見つからない場合は None。
    """
    which_path = shutil.which("tailscale")
    if which_path:
        return which_path

    # Windows 標準インストール先候補
    possible_paths = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Tailscale", "tailscale.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Tailscale", "tailscale.exe"),
        os.path.join(os.environ.get("LocalAppData", ""), "Tailscale", "tailscale.exe"),
    ]
    for p in possible_paths:
        if os.path.isfile(p):
            return p
    return None


def get_tailscale_info() -> Dict[str, any]:
    """
    Tailscale の導入状況、稼働状態、Tailscale IPv4、MagicDNS 名を取得する。
    戻り値:
    {
        "installed": bool,
        "running": bool,
        "ip": Optional[str],
        "dns_name": Optional[str],
        "binary_path": Optional[str],
        "error": Optional[str],
    }
    """
    ts_bin = find_tailscale_binary()
    if not ts_bin:
        return {
            "installed": False,
            "running": False,
            "backend_state": "NotInstalled",
            "ip": None,
            "dns_name": None,
            "binary_path": None,
            "error": "Tailscale CLI is not installed or not in PATH.",
        }

    try:
        res = subprocess.run(
            [ts_bin, "status", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        if res.returncode != 0:
            return {
                "installed": True,
                "running": False,
                "backend_state": "Stopped",
                "ip": None,
                "dns_name": None,
                "binary_path": ts_bin,
                "error": res.stderr.strip() or f"tailscale status exited with code {res.returncode}",
            }

        data = json.loads(res.stdout.strip())
        backend_state = data.get("BackendState", "")
        is_running = (backend_state == "Running")
        self_node = data.get("Self", {})
        dns_name = self_node.get("DNSName", "").rstrip(".")
        ips = self_node.get("TailscaleIPs", [])

        # IPv4 アドレスを抽出 (100.64.0.0/10)
        ts_ipv4 = None
        for ip_cand in ips:
            try:
                ip_obj = ipaddress.ip_address(ip_cand)
                if ip_obj.version == 4:
                    ts_ipv4 = ip_cand
                    break
            except ValueError:
                continue

        # もし JSON から取れなかった場合のフォールバック: tailscale ip -4
        if is_running and not ts_ipv4:
            ip_res = subprocess.run(
                [ts_bin, "ip", "-4"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            if ip_res.returncode == 0:
                ts_ipv4 = ip_res.stdout.strip()

        return {
            "installed": True,
            "running": is_running,
            "backend_state": backend_state,
            "ip": ts_ipv4,
            "dns_name": dns_name if dns_name else None,
            "binary_path": ts_bin,
            "error": None if is_running else f"Tailscale is not running (BackendState: {backend_state})",
        }
    except Exception as e:
        return {
            "installed": True,
            "running": False,
            "backend_state": "Error",
            "ip": None,
            "dns_name": None,
            "binary_path": ts_bin,
            "error": str(e),
        }


def download_and_install_tailscale(download_callback=None) -> Tuple[bool, str]:
    """
    Tailscale 公式最新インストーラー (tailscale-setup-latest.exe) をダウンロードし、
    Windows の管理者権限 (UAC 昇格) で起動する。
    戻り値: (成功フラグ, メッセージ)
    """
    if os.name != "nt":
        return False, "Tailscale の自動インストールは Windows 専用です。"

    ts_bin = find_tailscale_binary()
    if ts_bin:
        return True, f"Tailscale は既にインストールされています ({ts_bin})"

    url = "https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe"
    temp_dir = tempfile.gettempdir()
    installer_path = os.path.join(temp_dir, "tailscale-setup-latest.exe")

    try:
        if download_callback:
            download_callback("インストーラーをダウンロードしています (約25MB)...")
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Deinei-Launcher)"})
        with urllib.request.urlopen(req, timeout=30) as resp, open(installer_path, "wb") as out_file:
            shutil.copyfileobj(resp, out_file)

        if not os.path.isfile(installer_path) or os.path.getsize(installer_path) < 1024 * 1024:
            return False, "インストーラーのダウンロードサイズが不正です。"

        if download_callback:
            download_callback("UAC 昇格ダイアログを表示しています。画面の『はい』を押してください...")

        # PowerShell Start-Process -Verb RunAs で UAC 昇格して起動
        ps_cmd = (
            f"$proc = Start-Process -FilePath '{installer_path}' -Verb RunAs -PassThru; "
            "if ($proc) { $proc.WaitForExit(); exit $proc.ExitCode } else { exit 1 }"
        )
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )

        new_bin = find_tailscale_binary()
        if new_bin:
            return True, f"Tailscale のインストールが完了しました ({new_bin})"
        elif res.returncode != 0:
            return False, "インストールがユーザーによってキャンセルされたか、エラーが発生しました。"
        return True, "インストーラーが終了しました。Tailscale の起動を確認しています。"
    except Exception as e:
        return False, f"インストーラーの実行に失敗しました: {e}"


def trigger_tailscale_login() -> Tuple[bool, str]:
    """
    Tailscale CLI を呼び出し、ブラウザで認証画面 (login.tailscale.com) を起動する。
    戻り値: (成功フラグ, メッセージ)
    """
    ts_bin = find_tailscale_binary()
    if not ts_bin:
        return False, "Tailscale がインストールされていません。"

    try:
        subprocess.Popen(
            [ts_bin, "login"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return True, "ブラウザで Tailscale ログイン画面を開きました。認証を完了してください。"
    except Exception as e:
        return False, f"ログインコマンドの実行に失敗しました: {e}"
