"""
泥濘 (Deinei) ネットワーク公開マネージャー & 診断ツール (CLI)
--------------------------------------------------------------------------------
コマンド:
  python dei_net.py serve --lan [--port 8090] [--token <str>] [--no-qr]
  python dei_net.py serve --tailscale [--https] [--port 8090] [--token <str>] [--no-qr]
  python dei_net.py doctor

通信のローカル完結原則を遵守し、第三者中継サーバー (Cloudflare/ngrok等) は一切使用しません。
"""

import argparse
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import time
from typing import Optional, Tuple

# Windows コンソールでの UTF-8 出力を確実に有効化 (QRコードおよび日本語ログ用)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

from access_token import generate_token, sanitize_url_for_log
from gateway import DeineiGateway, find_available_port
from net_discovery import (
    get_best_lan_ip,
    get_lan_ip_candidates,
    get_network_profiles,
    get_tailscale_info,
)


def print_qr_code(url: str):
    """ターミナルに QR コードを出力する (segno を使用、未導入時はURL案内のみ)"""
    try:
        import segno
        qr = segno.make(url)
        print("\n" + "=" * 50)
        print(" [QRコード] スマートフォンのカメラで読み取ってください:")
        print("=" * 50)
        qr.terminal(compact=True)
        print("=" * 50 + "\n")
    except Exception as e:
        print(f"[INFO] QRコード表示スキップ (理由: {e})")


def check_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """指定ポートに TCP 接続可能か確認"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def check_http_health(host: str, port: int, path: str = "/health", timeout: float = 1.0) -> Tuple[bool, str]:
    """HTTP 接続で /health 等のエンドポイントの応答を確認"""
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", path)
        resp = conn.getresponse()
        status = resp.status
        conn.close()
        return (status == 200, f"HTTP {status}")
    except Exception as e:
        return (False, str(e))


def run_doctor():
    """システム診断コマンド"""
    print("\n========================================================")
    print(" 泥濘 (Deinei) ネットワーク・接続診断 (doctor)")
    print("========================================================\n")

    # 1. バックエンドサーバー状態の診断
    print("[1/4] バックエンド AI サーバー稼働状態:")
    backends = [
        ("LLM Server (WebUI & 推論)", "127.0.0.1", 8080, "/health"),
        ("Data Server (記憶/検索/会話)", "127.0.0.1", 5000, "/health"),
        ("TTS Server (音声合成)", "127.0.0.1", 5001, "/health"),
        ("Auth Gateway (外部端末中継)", "127.0.0.1", 8090, "/__deinei/health"),
    ]
    all_backends_ok = True
    for name, host, port, health_path in backends:
        is_open = check_port_open(host, port)
        if not is_open:
            if port == 8090:
                print(f"  - [INFO] {name} (ポート {port}): 停止中 (ローカル専用モード時は正常)")
            else:
                print(f"  - [WARN] {name} (ポート {port}): 未起動または接続不能")
                all_backends_ok = False
        else:
            ok, msg = check_http_health(host, port, health_path)
            if ok:
                print(f"  - [OK]   {name} (ポート {port}): 正常稼働中 ({msg})")
            else:
                print(f"  - [WARN] {name} (ポート {port}): ポート開放中ですがヘルスチェック応答に注意 ({msg})")
                if port != 8090:
                    all_backends_ok = False

    if not all_backends_ok:
        print("    -> ヒント: start_ai_servers.bat を先に実行してバックエンドを起動してください。")

    # 2. LAN IP アドレス候補の診断
    print("\n[2/4] LAN IPv4 アドレス候補 (仮想アダプタ・リンクローカル除外済み):")
    candidates = get_lan_ip_candidates()
    if candidates:
        for idx, c in enumerate(candidates):
            rec = " (推奨)" if idx == 0 else ""
            print(f"  - [OK] {c['interface']}: {c['ip']}{rec}")
    else:
        print("  - [WARN] 有効なプライベート LAN IPv4 が検出されませんでした。Wi-FiまたはLANケーブル接続を確認してください。")

    # 3. Windows ネットワークプロファイル診断 (ファイアウォール遮断リスク)
    print("\n[3/4] Windows ネットワークプロファイル診断:")
    profiles = get_network_profiles()
    public_found = False
    if profiles:
        for p in profiles:
            cat = p.get("category", "Unknown")
            is_pub = (cat.lower() == "public")
            mark = "[WARN]" if is_pub else "[OK]"
            print(f"  - {mark} 接続名: '{p.get('name')}' (インターフェース: {p.get('interface')}) -> カテゴリ: {cat}")
            if is_pub:
                public_found = True
        if public_found:
            print("    ------------------------------------------------------------------")
            print("    [!] 注意: ネットワークが 'Public' (パブリック) に設定されていると、")
            print("        Windows Defender ファイアウォールによって同一Wi-Fiのスマホ等からの")
            print("        着信接続が自動遮断され、接続できないことがあります。")
            print("        自宅の信頼できるWi-Fiの場合は、管理者権限の PowerShell で以下を実行し、")
            print("        プロファイルを 'Private' (プライベート) に変更することを推奨します:")
            print("        > Set-NetConnectionProfile -InterfaceAlias 'Wi-Fi' -NetworkCategory Private")
            print("    ------------------------------------------------------------------")
    else:
        print("  - [INFO] ネットワークプロファイル情報を取得できませんでした。")

    # 4. Tailscale 状態の診断
    print("\n[4/4] Tailscale 連携状態:")
    ts = get_tailscale_info()
    if not ts.get("installed"):
        print("  - [INFO] Tailscale CLI は検出されませんでした (未インストール)。")
        print("    -> Tailscale によるセキュアな外出先アクセスを利用する場合は、公式サイトより導入してください:")
        print("       https://tailscale.com/download")
    else:
        print(f"  - [OK] Tailscale CLI 検出: {ts.get('binary_path')}")
        if ts.get("running"):
            print(f"  - [OK] Tailscale 状態: 稼働中 (Running)")
            print(f"  - [OK] Tailscale IPv4: {ts.get('ip')}")
            if ts.get("dns_name"):
                print(f"  - [OK] MagicDNS 名:     {ts.get('dns_name')}")
        else:
            print(f"  - [WARN] Tailscale はインストールされていますが、稼働していません。")
            print(f"    理由/状態: {ts.get('error')}")
            print("    -> 'tailscale up' または Tailscale アプリでログインして接続を有効化してください。")

    print("\n========================================================\n")


def run_serve(args):
    """ゲートウェイサーバー起動コマンド"""
    is_lan = args.lan
    is_tailscale = args.tailscale
    is_https = False

    if not is_lan and not is_tailscale:
        print("[ERROR] 起動モードを指定してください (--lan または --tailscale)。")
        sys.exit(1)

    # トークンの特定または生成
    token = args.token or os.environ.get("DEINEI_ACCESS_TOKEN", "").strip() or generate_token()

    allowed_hosts = {"127.0.0.1", "localhost"}
    bind_host = "127.0.0.1"
    best_ip = None
    ts_ip = None
    dns_name = None

    if is_lan:
        bind_host = "0.0.0.0"
        candidates = get_lan_ip_candidates()
        best_ip = candidates[0]["ip"] if candidates else None

        if not best_ip:
            print("[WARN] 有効な LAN IP アドレスを検出できませんでした。フォールバックとして 0.0.0.0 で待受を開始します。")
        else:
            for c in candidates:
                allowed_hosts.add(c["ip"])

    elif is_tailscale:
        ts = get_tailscale_info()
        if not ts.get("installed"):
            print("[ERROR] Tailscale CLI がインストールされていないか、PATH に通っていません。")
            print("[INFO]  公式サイトよりダウンロードしてインストールしてください: https://tailscale.com/download")
            sys.exit(1)
        if not ts.get("running") or not ts.get("ip"):
            print("[ERROR] Tailscale が起動していないか、ログインしていません。")
            print(f"[INFO]  状態詳細: {ts.get('error')}")
            print("[INFO]  'tailscale up' を実行するか、タスクトレイの Tailscale からログインしてください。")
            sys.exit(1)

        ts_ip = ts.get("ip")
        dns_name = ts.get("dns_name")
        bind_host = ts_ip
        allowed_hosts.add(ts_ip)
        if dns_name:
            allowed_hosts.add(dns_name.lower())

    # bind_host 確定後に待受ポートを決定 (要求ポートが塞がっている場合は自動スライド)
    desired_port = args.port
    try:
        actual_port = find_available_port(bind_host, desired_port)
    except RuntimeError as e:
        # [S04/S05 fix 補完] 全候補ポートが使用中または権限除外の場合は起動を断念する
        print(f"[ERROR] 待受ポートの確保に失敗しました: {e}")
        sys.exit(1)
    if actual_port != desired_port:
        print(f"[INFO] 要求ポート {desired_port} は使用中のため、空きポート {actual_port} に自動スライドしました。")
    port = actual_port

    # 確定した port で display_urls を構築
    display_urls = []
    if is_lan:
        if not best_ip:
            display_urls.append(f"http://<PCのIPアドレス>:{port}/?token={token}")
        else:
            display_urls.append(f"http://{best_ip}:{port}/?token={token}")
    elif is_tailscale:
        if args.https:
            is_https = True
            print(f"[INFO] Tailscale Serve HTTPS 連携モードを有効化します (Port: {port})")
            if dns_name:
                display_urls.append(f"https://{dns_name}/?token={token}")
            else:
                display_urls.append(f"https://{ts_ip}/?token={token}")
        else:
            display_urls.append(f"http://{ts_ip}:{port}/?token={token}")
            if dns_name:
                display_urls.append(f"http://{dns_name}:{port}/?token={token}")

    # ヘッダー情報の表示
    mode_name = "LAN 共有 (同一Wi-Fi)" if is_lan else ("Tailscale (セキュアVPN/メッシュ)" + (" + HTTPS" if is_https else ""))
    print("\n" + "=" * 60)
    print(f" 泥濘 (Deinei) セキュア・ゲートウェイ起動")
    print("=" * 60)
    print(f" - モード:       {mode_name}")
    print(f" - 待受アドレス: {bind_host}:{port}")
    print(f" - バックエンド: 8080 (LLM), 5000 (Flask), 5001 (TTS)")
    print(f" - 外部中継:     なし (ローカル端末間直接通信)")
    print("=" * 60)

    primary_url = display_urls[0]
    print(f"\n[接続用URL (スマートフォン・他端末)]:")
    for u in display_urls:
        print(f"  >> {u}")

    print("\n※ 初回アクセス時にトークンが自動でブラウザCookieに安全に登録され、")
    print("   URLバーからトークンが自動除去されます。")

    if not args.no_qr:
        print_qr_code(primary_url)

    sys.stdout.flush()

    # ゲートウェイの起動
    gateway = DeineiGateway(
        bind_host=bind_host,
        port=port,
        expected_token=token,
        target_llama_port=args.llama_port,
        target_flask_port=args.flask_port,
        target_tts_port=args.tts_port,
        allowed_hosts=allowed_hosts,
        is_https=is_https,
    )

    try:
        gateway.start(blocking=True)
    except KeyboardInterrupt:
        print("\n[INFO] ゲートウェイを終了しています...", flush=True)
    except Exception as e:
        print(f"\n[ERROR] ゲートウェイ実行エラー: {e}", file=sys.stderr, flush=True)
        raise
    finally:
        gateway.stop()


def main():
    parser = argparse.ArgumentParser(description="泥濘 (Deinei) ネットワーク公開マネージャー & 診断ツール")
    subparsers = parser.add_subparsers(dest="command", help="サブコマンド")

    # serve サブコマンド
    serve_parser = subparsers.add_parser("serve", help="認証ゲートウェイを起動")
    mode_group = serve_parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--lan", action="store_true", help="LAN (同一Wi-Fi) からのアクセスを許可 (0.0.0.0 待受)")
    mode_group.add_argument("--tailscale", action="store_true", help="Tailscale IP のみで待受")

    serve_parser.add_argument("--https", action="store_true", help="Tailscale HTTPS 構成に対応 (Cookie Secure 属性を付与)")
    serve_parser.add_argument("--port", type=int, default=8090, help="ゲートウェイの待受ポート (既定: 8090)")
    serve_parser.add_argument("--token", type=str, default=None, help="固定アクセストークン (未指定時はランダム生成)")
    serve_parser.add_argument("--no-qr", action="store_true", help="QRコード表示をスキップ")
    serve_parser.add_argument("--llama-port", type=int, default=8080, help="転送先 LLM ポート (既定: 8080)")
    serve_parser.add_argument("--flask-port", type=int, default=5000, help="転送先 Flask ポート (既定: 5000)")
    serve_parser.add_argument("--tts-port", type=int, default=5001, help="転送先 TTS ポート (既定: 5001)")

    # doctor サブコマンド
    subparsers.add_parser("doctor", help="ポート、ネットワークプロファイル、Tailscale 状態を診断")

    args = parser.parse_args()

    if args.command == "doctor":
        run_doctor()
    elif args.command == "serve":
        run_serve(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
