"""
泥濘 (Deinei) gateway 単体 & 結合テスト
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
import unittest
import urllib.request
import urllib.error

# backend/data をインポートパスに追加
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(REPO_ROOT, "backend", "data")
if DATA_DIR not in sys.path:
    sys.path.insert(0, DATA_DIR)

from gateway import DeineiGateway, ThreadingHTTPServer


# テスト用ダミーバックエンドハンドラ
class MockBackendHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # テスト出力を静穏化

    def do_GET(self):
        if self.path == "/health":
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        elif self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for i in range(3):
                self.wfile.write(f"data: chunk {i}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.05)
        elif self.path == "/headers-inspect":
            payload = json.dumps(
                {
                    "origins": self.headers.get_all("Origin") or [],
                    "referers": self.headers.get_all("Referer") or [],
                    "xffs": self.headers.get_all("X-Forwarded-For") or [],
                    "hosts": self.headers.get_all("Host") or [],
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()
        elif self.path == "/chunked":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            chunk_body = b"hello chunked"
            self.wfile.write(f"{len(chunk_body):X}\r\n".encode("ascii") + chunk_body + b"\r\n0\r\n\r\n")
            self.wfile.flush()
        else:
            body = f"Response from port {self.server.server_port}: {self.path}".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Echo-Path", self.path)
            self.send_header("X-Echo-Origin", self.headers.get("Origin", ""))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        self.close_connection = True

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body_in = self.rfile.read(content_length).decode("utf-8")
        body_out = json.dumps({"echo_body": body_in, "path": self.path}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body_out)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body_out)
        self.wfile.flush()
        self.close_connection = True


class TestGateway(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 1. ダミーのバックエンドサーバー3種を起動
        cls.mock_llama = ThreadingHTTPServer(("127.0.0.1", 0), MockBackendHandler)
        cls.mock_flask = ThreadingHTTPServer(("127.0.0.1", 0), MockBackendHandler)
        cls.mock_tts = ThreadingHTTPServer(("127.0.0.1", 0), MockBackendHandler)

        cls.port_llama = cls.mock_llama.server_port
        cls.port_flask = cls.mock_flask.server_port
        cls.port_tts = cls.mock_tts.server_port

        cls.t_llama = threading.Thread(target=cls.mock_llama.serve_forever, daemon=True)
        cls.t_flask = threading.Thread(target=cls.mock_flask.serve_forever, daemon=True)
        cls.t_tts = threading.Thread(target=cls.mock_tts.serve_forever, daemon=True)

        cls.t_llama.start()
        cls.t_flask.start()
        cls.t_tts.start()

        # 2. ゲートウェイを起動
        cls.test_token = "secure_test_token_12345"
        cls.gateway = DeineiGateway(
            bind_host="127.0.0.1",
            port=0,  # 空きポート自動割り当て
            expected_token=cls.test_token,
            target_llama_port=cls.port_llama,
            target_flask_port=cls.port_flask,
            target_tts_port=cls.port_tts,
            allowed_hosts={"127.0.0.1", "localhost", "mytest.local"},
        )
        cls.gateway.start(blocking=False)
        cls.gateway_port = cls.gateway.server.server_port
        cls.base_url = f"http://127.0.0.1:{cls.gateway_port}"

    @classmethod
    def tearDownClass(cls):
        cls.gateway.stop()
        cls.mock_llama.shutdown()
        cls.mock_llama.server_close()
        cls.mock_flask.shutdown()
        cls.mock_flask.server_close()
        cls.mock_tts.shutdown()
        cls.mock_tts.server_close()

    def test_01_unauthorized_without_token(self):
        req = urllib.request.Request(f"{self.base_url}/")
        try:
            urllib.request.urlopen(req)
            self.fail("Expected HTTPError 401")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 401)
            self.assertIn("WWW-Authenticate", e.headers)

    def test_02_unauthorized_with_wrong_token(self):
        req = urllib.request.Request(f"{self.base_url}/?token=wrong_token")
        try:
            urllib.request.urlopen(req)
            self.fail("Expected HTTPError 401")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 401)

    def test_03_token_redirect_and_cookie(self):
        # 1. 正しいトークン付きURLへアクセス -> 302 リダイレクトと Set-Cookie の検証
        conn = http.client.HTTPConnection("127.0.0.1", self.gateway_port)
        conn.request("GET", f"/?token={self.test_token}")
        resp = conn.getresponse()

        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.getheader("Location"), "/")
        set_cookie = resp.getheader("Set-Cookie")
        self.assertIsNotNone(set_cookie)
        self.assertIn(f"deinei_token={self.test_token}", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        conn.close()

        # 2. 取得した Cookie を使って通常アクセス -> 200 OK
        conn = http.client.HTTPConnection("127.0.0.1", self.gateway_port)
        conn.request("GET", "/", headers={"Cookie": f"deinei_token={self.test_token}"})
        resp2 = conn.getresponse()
        self.assertEqual(resp2.status, 200)
        body = resp2.read().decode("utf-8")
        self.assertIn(f"Response from port {self.port_llama}: /", body)
        conn.close()

    def test_04_bearer_auth(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.gateway_port)
        conn.request("GET", "/health", headers={"Authorization": f"Bearer {self.test_token}"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(data.get("status"), "ok")
        conn.close()

    def test_05_path_routing(self):
        # /__deinei/api/memory/all -> Flask ポートへ /memory/all として転送
        conn = http.client.HTTPConnection("127.0.0.1", self.gateway_port)
        conn.request("GET", "/__deinei/api/memory/all", headers={"Authorization": f"Bearer {self.test_token}"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("X-Echo-Path"), "/memory/all")
        self.assertEqual(resp.getheader("X-Echo-Origin"), "http://127.0.0.1:8080")
        conn.close()

        # /__deinei/tts/audio -> TTS ポートへ /audio として転送
        conn = http.client.HTTPConnection("127.0.0.1", self.gateway_port)
        conn.request("GET", "/__deinei/tts/audio", headers={"Authorization": f"Bearer {self.test_token}"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("X-Echo-Path"), "/audio")
        conn.close()

    def test_06_post_body_proxy(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.gateway_port)
        payload = json.dumps({"query": "hello world"})
        conn.request(
            "POST",
            "/__deinei/api/search",
            body=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.test_token}",
            },
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        res_data = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(res_data.get("echo_body"), payload)
        self.assertEqual(res_data.get("path"), "/search")
        conn.close()

    def test_07_sse_streaming(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.gateway_port)
        conn.request("GET", "/stream", headers={"Authorization": f"Bearer {self.test_token}"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertIn("text/event-stream", resp.getheader("Content-Type", ""))

        chunks = []
        for _ in range(6):  # 3 chunks * 2 lines (data: chunk X + empty line)
            line = resp.readline()
            if not line:
                break
            chunks.append(line.decode("utf-8"))
        all_text = "".join(chunks)
        self.assertIn("data: chunk 0", all_text)
        self.assertIn("data: chunk 1", all_text)
        self.assertIn("data: chunk 2", all_text)
        conn.close()

    def test_08_dns_rebinding_defense(self):
        # 許可されていない Host ヘッダーでのアクセス
        conn = http.client.HTTPConnection("127.0.0.1", self.gateway_port)
        conn.request(
            "GET",
            "/",
            headers={
                "Host": "evil.attacker.com",
                "Authorization": f"Bearer {self.test_token}",
            },
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 400)
        conn.close()

    def test_09_case_insensitive_header_overwrite(self):
        # クライアントが小文字の origin / referer / x-forwarded-for を送信しても重複せず上書きされること
        conn = http.client.HTTPConnection("127.0.0.1", self.gateway_port)
        conn.request(
            "GET",
            "/__deinei/api/headers-inspect",
            headers={
                "Authorization": f"Bearer {self.test_token}",
                "origin": "http://evil.example",
                "referer": "http://evil.example/page",
                "x-forwarded-for": "6.6.6.6",
            },
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(data["origins"], ["http://127.0.0.1:8080"])
        self.assertEqual(data["referers"], ["http://127.0.0.1:8080/"])
        self.assertEqual(data["xffs"], ["127.0.0.1"])
        self.assertEqual(len(data["hosts"]), 1)
        conn.close()

    def test_10_gateway_self_health_endpoint(self):
        # Doctor 用の /__deinei/health が認証なしで 200 OK を返すこと
        conn = http.client.HTTPConnection("127.0.0.1", self.gateway_port)
        conn.request("GET", "/__deinei/health")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("service"), "deinei-gateway")
        conn.close()

    def test_11_body_size_limit(self):
        # 負の Content-Length は 400、上限超過の Content-Length は 413 を返すこと
        conn = http.client.HTTPConnection("127.0.0.1", self.gateway_port)
        conn.putrequest("POST", "/__deinei/api/search")
        conn.putheader("Authorization", f"Bearer {self.test_token}")
        conn.putheader("Content-Length", str(200 * 1024 * 1024))
        conn.endheaders()
        resp = conn.getresponse()
        self.assertEqual(resp.status, 413)
        conn.close()

    def test_12_chunked_response_framing(self):
        # バックエンドが chunked で返した非SSEレスポンスに Connection: close が付与されること
        conn = http.client.HTTPConnection("127.0.0.1", self.gateway_port)
        conn.request("GET", "/chunked", headers={"Authorization": f"Bearer {self.test_token}"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Connection", "").lower(), "close")
        body = resp.read()
        self.assertEqual(body, b"hello chunked")
        conn.close()


    def test_13_conflicting_content_length_rejected(self):
        # 矛盾・重複する Content-Length は 400 で拒否されること
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("127.0.0.1", self.gateway_port))
        req = (
            f"POST /__deinei/api/search HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.gateway_port}\r\n"
            f"Authorization: Bearer {self.test_token}\r\n"
            f"Content-Length: 5\r\n"
            f"Content-Length: 10\r\n"
            f"\r\n"
            f"hello"
        ).encode("utf-8")
        s.sendall(req)
        resp = s.recv(1024).decode("utf-8", errors="replace")
        s.close()
        self.assertTrue("400 " in resp or resp.startswith("HTTP/1.1 400"), f"Expected 400, got: {resp}")

    def test_14_chunked_request_rejected(self):
        # Transfer-Encoding: chunked リクエストは 411 で要求されること
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("127.0.0.1", self.gateway_port))
        req = (
            f"POST /__deinei/api/search HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.gateway_port}\r\n"
            f"Authorization: Bearer {self.test_token}\r\n"
            f"Transfer-Encoding: chunked\r\n"
            f"\r\n"
            f"5\r\nhello\r\n0\r\n\r\n"
        ).encode("utf-8")
        s.sendall(req)
        resp = s.recv(1024).decode("utf-8", errors="replace")
        s.close()
        self.assertTrue("411 " in resp or resp.startswith("HTTP/1.1 411"), f"Expected 411, got: {resp}")

    def test_15_truncated_body_rejected(self):
        # クライアント途中切断による短縮ボディは 400 で拒否されること
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("127.0.0.1", self.gateway_port))
        req = (
            f"POST /__deinei/api/search HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.gateway_port}\r\n"
            f"Authorization: Bearer {self.test_token}\r\n"
            f"Content-Length: 100\r\n"
            f"\r\n"
            f"short"
        ).encode("utf-8")
        s.sendall(req)
        s.shutdown(socket.SHUT_WR)
        resp = s.recv(1024).decode("utf-8", errors="replace")
        s.close()
        self.assertTrue("400 " in resp or resp.startswith("HTTP/1.1 400"), f"Expected 400, got: {resp}")


if __name__ == "__main__":
    unittest.main()
