"""
泥濘 (Deinei) 認証リバースプロキシ・ゲートウェイ
--------------------------------------------------------------------------------
スマートフォン等の外部端末からのアクセスを単一ポート (既定: 8090) で受け付け、
トークン認証 (URL / Cookie / Bearer) を実施した上で、
ローカルの各バックエンドサーバーに安全にリバースプロキシする。

- /               -> 127.0.0.1:8080 (llama-server: UI & LLM API)
- /__deinei/api/* -> 127.0.0.1:5000/* (Flask: 記憶/会話/検索)
- /__deinei/tts/* -> 127.0.0.1:5001/* (FastAPI: 音声合成)

外部通信ライブラリに依存せず Python 標準ライブラリ (http.server / http.client) のみで動作。
SSE (Server-Sent Events) の即時ストリーミングに対応。
"""

import http.client
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
import socket
import socketserver
import sys
import threading
import time
import urllib.parse
from typing import Optional, Tuple

# 同一ディレクトリの access_token モジュールをインポート
_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

from access_token import (
    generate_token,
    verify_token,
    extract_token_from_cookie,
    extract_token_from_auth_header,
    extract_token_from_query,
    make_auth_cookie,
    sanitize_url_for_log,
    strip_token_from_url,
)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    """リクエストごとにスレッドを生成するマルチスレッドHTTPサーバー"""
    daemon_threads = True
    # [S05 fix] Windows の SO_REUSEADDR は既存リスナーへの二重バインドを許すため使わず、
    # SO_EXCLUSIVEADDRUSE で乗っ取りを拒否する (TIME_WAIT は許容)。
    # 非 Windows は従来どおり SO_REUSEADDR を用いて TIME_WAIT 再バインドを許容する。
    allow_reuse_address = False

    def server_bind(self):
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        HTTPServer.server_bind(self)


class DeineiGatewayHandler(BaseHTTPRequestHandler):
    # サーバー側設定 (DeineiGateway 起動時にクラス変数へ注入される)
    expected_token: str = ""
    target_llama_port: int = 8080
    target_flask_port: int = 5000
    target_tts_port: int = 5001
    allowed_hosts: set = set()
    is_https: bool = False

    # HTTP/1.1 対応およびソケット読み取りタイムアウト・ボディサイズ上限
    protocol_version = "HTTP/1.1"
    timeout = 60
    max_body_bytes = 64 * 1024 * 1024

    def log_message(self, format, *args):
        """標準のアクセスログ出力をオーバーライドし、トークンをマスクした上で出力"""
        sanitized_msg = sanitize_url_for_log(format % args)
        print(f"[INFO] Gateway: {self.client_address[0]} - {sanitized_msg}")

    def log_error(self, format, *args):
        sanitized_msg = sanitize_url_for_log(format % args)
        print(f"[ERROR] Gateway: {self.client_address[0]} - {sanitized_msg}", file=sys.stderr)

    def _send_unauthorized(self):
        """認証失敗時のレスポンス (401)"""
        body = (
            "<!doctype html>\n"
            "<html lang='ja'>\n"
            "<head><meta charset='utf-8'><title>Deinei: 認証が必要です</title>"
            "<style>body { font-family: sans-serif; background: #121214; color: #e1e1e6; padding: 40px; text-align: center; }"
            ".box { max-width: 500px; margin: 0 auto; background: #1e1e24; padding: 24px; border-radius: 8px; border: 1px solid #333; }"
            "h2 { color: #ff79c6; margin-top: 0; }"
            "p { line-height: 1.6; color: #aaa; }"
            "code { background: #282a36; padding: 2px 6px; border-radius: 4px; color: #50fa7b; }"
            "</style></head>\n"
            "<body><div class='box'>"
            "<h2>[Deinei] 認証が必要です</h2>"
            "<p>このサーバーへのアクセスには有効なアクセストークンが必要です。</p>"
            "<p>サーバー起動時のターミナルに表示された <strong>QRコード</strong> または <strong>トークン付きURL</strong> (<code>?token=...</code>) からアクセスしてください。</p>"
            "</div></body></html>"
        ).encode("utf-8")

        self.send_response(401)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("WWW-Authenticate", 'Bearer realm="Deinei Gateway"')
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()
        self.close_connection = True

    def _check_host_header(self) -> bool:
        """
        DNS Rebinding 攻撃対策: Host ヘッダーが許可リストに含まれているか検証。
        allowed_hosts が空の場合は検証をスキップ。
        """
        if not self.allowed_hosts:
            return True
        host_hdr = self.headers.get("Host", "").split(":")[0].strip().lower()
        if not host_hdr:
            return False
        # 127.0.0.1, localhost は常に許可
        if host_hdr in {"127.0.0.1", "localhost"}:
            return True
        return host_hdr in self.allowed_hosts

    def _authenticate(self) -> Tuple[bool, Optional[str]]:
        """
        リクエストを認証する。
        戻り値: (認証成功フラグ, リダイレクト先URLまたはNone)
        """
        # 1. クエリパラメータの token を確認
        parsed = urllib.parse.urlsplit(self.path)
        query_token = extract_token_from_query(parsed.query)
        if query_token:
            if verify_token(self.expected_token, query_token):
                # 認証成功: Cookie をセットしてトークン無しURLへ 302 リダイレクト
                redirect_url = strip_token_from_url(self.path)
                return True, redirect_url
            else:
                return False, None

        # 2. Cookie ヘッダーの deinei_token を確認
        cookie_header = self.headers.get("Cookie")
        cookie_token = extract_token_from_cookie(cookie_header)
        if cookie_token and verify_token(self.expected_token, cookie_token):
            return True, None

        # 3. Authorization: Bearer ヘッダーを確認
        auth_header = self.headers.get("Authorization")
        bearer_token = extract_token_from_auth_header(auth_header)
        if bearer_token and verify_token(self.expected_token, bearer_token):
            return True, None

        return False, None

    def _resolve_target(self, raw_path: str) -> Tuple[int, str]:
        """
        リクエストパスからバックエンドポートと転送先パスを解決する。
        - /__deinei/api/* -> (5000, /*)
        - /__deinei/tts/* -> (5001, /*)
        - それ以外        -> (8080, raw_path)
        """
        if raw_path == "/__deinei/api" or raw_path.startswith("/__deinei/api/"):
            target_path = raw_path[len("/__deinei/api"):]
            if not target_path.startswith("/"):
                target_path = "/" + target_path
            return self.target_flask_port, target_path
        elif raw_path == "/__deinei/tts" or raw_path.startswith("/__deinei/tts/"):
            target_path = raw_path[len("/__deinei/tts"):]
            if not target_path.startswith("/"):
                target_path = "/" + target_path
            return self.target_tts_port, target_path
        else:
            return self.target_llama_port, raw_path

    def _proxy_request(self, method: str):
        # 1. Host ヘッダーのセキュリティ検証
        if not self._check_host_header():
            print(f"[WARN] Gateway: 不正な Host ヘッダーによるアクセスを拒否しました: {self.headers.get('Host')}")
            self.send_error(400, "Invalid Host header")
            return

        # Doctor 接続診断用ゲートウェイ死活確認エンドポイント
        parsed_req = urllib.parse.urlsplit(self.path)
        if parsed_req.path == "/__deinei/health" and method in ("GET", "HEAD"):
            h_body = b'{"status":"ok","service":"deinei-gateway"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(h_body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(h_body)
            self.wfile.flush()
            self.close_connection = True
            return

        # 2. 認証チェック
        authenticated, redirect_url = self._authenticate()
        if not authenticated:
            self._send_unauthorized()
            return

        # 初回クエリートークン検証成功時の 302 リダイレクト処理
        if redirect_url:
            cookie_val = make_auth_cookie(self.expected_token, secure=self.is_https)
            self.send_response(302)
            self.send_header("Location", redirect_url)
            self.send_header("Set-Cookie", cookie_val)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            return

        # 3. 転送先の決定
        target_port, target_path = self._resolve_target(self.path)

        # 4. リクエストボディの読み込み (サイズ上限・負数検証付き)
        content_length = self.headers.get("Content-Length")
        # [S01 fix] 重複・矛盾する Content-Length を拒否 (ボディ切断・desync 対策)
        _cl_values = self.headers.get_all("Content-Length") or []
        _cl_uniq = {v.strip() for v in _cl_values}
        if len(_cl_uniq) > 1:
            self.send_error(400, "Conflicting or duplicate Content-Length headers")
            self.close_connection = True
            return
        # [S02 fix] chunked 等の Transfer-Encoding フレーミングは未対応のため 411 で要求
        # (現状はボディを黙って消失させてしまう。応答側 SSE には影響しない)
        _te_values = [v.strip().lower() for v in (self.headers.get_all("Transfer-Encoding") or []) if v and v.strip()]
        if any(v != "identity" for v in _te_values):
            self.send_error(411, "Length Required: request bodies must use Content-Length")
            self.close_connection = True
            return
        body = None
        if content_length is not None:
            try:
                length_val = int(content_length)
            except ValueError:
                self.send_error(400, "Invalid Content-Length header")
                return
            if length_val < 0:
                self.send_error(400, "Negative Content-Length header")
                return
            if length_val > self.max_body_bytes:
                self.send_error(413, "Request body exceeds maximum allowed size")
                return
            if length_val > 0:
                try:
                    body = self.rfile.read(length_val)
                except Exception as e:
                    self.send_error(400, f"Error reading request body: {e}")
                    return
                if len(body) != length_val:
                    # [S01 fix 補完] クライアント切断でバイト数が足りない場合、S01 と同型の
                    # desync (バックエンドが CL 分の残バイト待ちでブロック) を防ぐため 400 とする
                    self.send_error(400, "Truncated request body (client disconnect)")
                    self.close_connection = True
                    return

        # 5. バックエンドヘッダーの構築
        # ゲートウェイ側で上書きするヘッダーおよびホップバイホップヘッダーを大小文字問わず除外
        stripped_headers = {
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailers",
            "transfer-encoding",
            "upgrade",
            "host",
            "origin",
            "referer",
            "x-forwarded-for",
            "x-forwarded-proto",
            "x-forwarded-host",
        }
        target_headers = {}
        for k, v in self.headers.items():
            if k.lower() in stripped_headers:
                continue
            target_headers[k] = v

        # バックエンドに対する Host ヘッダーとオリジン整合性の設定
        target_headers["Host"] = f"127.0.0.1:{target_port}"
        target_headers["X-Forwarded-For"] = self.client_address[0]
        target_headers["X-Forwarded-Proto"] = "https" if self.is_https else "http"

        # server.py / tts_server.py の厳格な CSRF / Origin 検査を通過させるため、内部オリジンを設定
        # (すでにゲートウェイ側でトークン認証が完了しているため安全)
        target_headers["Origin"] = "http://127.0.0.1:8080"
        target_headers["Referer"] = "http://127.0.0.1:8080/"

        # 6. バックエンドへの HTTP 接続と転送
        conn = None
        try:
            conn = http.client.HTTPConnection("127.0.0.1", target_port, timeout=300)
            conn.request(method, target_path, body=body, headers=target_headers)
            resp = conn.getresponse()

            # レスポンスヘッダーの解析
            is_sse = False
            resp_headers = []
            for hk, hv in resp.getheaders():
                hk_lower = hk.lower()
                if hk_lower == "content-type" and "text/event-stream" in hv.lower():
                    is_sse = True
                # 不要なホップバイホップヘッダーを除外
                if hk_lower in {"connection", "keep-alive", "transfer-encoding"}:
                    continue
                resp_headers.append((hk, hv))

            # クライアントへステータスコードを送信
            self.send_response(resp.status, resp.reason)
            for hk, hv in resp_headers:
                self.send_header(hk, hv)

            if is_sse:
                # SSE ストリーミング転送: バッファリングせず即座にフラッシュ
                self.send_header("Connection", "keep-alive")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()

                while True:
                    chunk = resp.read(1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                self.close_connection = True
            else:
                # 通常レスポンス転送: Connection: close でフレーミング終端を明示
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                self.wfile.flush()
                self.close_connection = True

        except (BrokenPipeError, ConnectionResetError):
            # クライアント側が切断した場合 (通常の切断動作)
            pass
        except Exception as e:
            self.log_error("Proxy error to port %s: %s", target_port, e)
            try:
                self.send_error(502, f"Bad Gateway: backend service on port {target_port} is unavailable")
            except Exception:
                pass
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def do_GET(self):
        self._proxy_request("GET")

    def do_POST(self):
        self._proxy_request("POST")

    def do_PUT(self):
        self._proxy_request("PUT")

    def do_DELETE(self):
        self._proxy_request("DELETE")

    def do_OPTIONS(self):
        # ゲートウェイは同一オリジンとして振る舞うため、プリフライトが来た場合も透過転送
        self._proxy_request("OPTIONS")

    def do_HEAD(self):
        self._proxy_request("HEAD")



def find_available_port(host: str = "127.0.0.1", start_port: int = 8090, max_attempts: int = 20) -> int:
    """指定されたホストとポートから開始し、バインド可能な空きポートを探索して返す。"""
    import socket
    test_host = host if host not in ("0.0.0.0", "") else "0.0.0.0"
    for p in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # [S05 fix] プローブには SO_REUSEADDR を付けない (既存リスナーを『空き』と誤認するため)
            try:
                s.bind((test_host, p))
                return p
            except OSError:
                continue
    # [S05 fix] 枯渇時は誤って使用中ポートを返さず RuntimeError
    raise RuntimeError(f"ポート {start_port} から {start_port + max_attempts - 1} までの全ポートが使用中です")

class DeineiGateway:
    def __init__(
        self,
        bind_host: str = "0.0.0.0",
        port: int = 8090,
        expected_token: Optional[str] = None,
        target_llama_port: int = 8080,
        target_flask_port: int = 5000,
        target_tts_port: int = 5001,
        allowed_hosts: Optional[set] = None,
        is_https: bool = False,
    ):
        self.bind_host = bind_host
        self.port = port
        self.expected_token = expected_token or generate_token()
        self.target_llama_port = target_llama_port
        self.target_flask_port = target_flask_port
        self.target_tts_port = target_tts_port
        self.allowed_hosts = allowed_hosts or set()
        self.is_https = is_https

        # ハンドラクラス変数の設定
        DeineiGatewayHandler.expected_token = self.expected_token
        DeineiGatewayHandler.target_llama_port = self.target_llama_port
        DeineiGatewayHandler.target_flask_port = self.target_flask_port
        DeineiGatewayHandler.target_tts_port = self.target_tts_port
        DeineiGatewayHandler.allowed_hosts = {h.lower() for h in self.allowed_hosts}
        DeineiGatewayHandler.is_https = self.is_https

        self.server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self, blocking: bool = True, max_port_attempts: int = 20):
        actual_port = self.port
        for attempt in range(max_port_attempts):
            try:
                self.server = ThreadingHTTPServer((self.bind_host, actual_port), DeineiGatewayHandler)
                self.port = actual_port
                break
            except OSError as e:
                if getattr(e, 'winerror', None) in (10048, 10013) or 'address already in use' in str(e).lower() or isinstance(e, PermissionError):
                    print(f"[WARN] Gateway ポート {actual_port} は別プロセスで使用中のため、ポート {actual_port + 1} を試行します...")
                    actual_port += 1
                else:
                    raise
        else:
            raise RuntimeError(f"ポート {self.port} から {actual_port - 1} までの全ポートが使用中です")

        print(f"[INFO] Deinei Gateway listening on {self.bind_host}:{self.port}")
        if blocking:
            try:
                self.server.serve_forever()
            except KeyboardInterrupt:
                print("\n[INFO] Gateway shutting down...")
            finally:
                self.server.server_close()
        else:
            self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self._thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            if self._thread:
                self._thread.join(timeout=3)
            print("[INFO] Gateway stopped.")
