"""
泥濘 (Deinei) access_token 単体テスト
"""

import os
import sys
import unittest

# backend/data をインポートパスに追加
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(REPO_ROOT, "backend", "data")
if DATA_DIR not in sys.path:
    sys.path.insert(0, DATA_DIR)

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


class TestAccessToken(unittest.TestCase):
    def test_generate_and_verify(self):
        t1 = generate_token()
        self.assertTrue(len(t1) >= 32)
        self.assertTrue(verify_token(t1, t1))
        self.assertFalse(verify_token(t1, "wrong_token"))
        self.assertFalse(verify_token(t1, None))
        self.assertFalse(verify_token(t1, ""))

    def test_env_token_override(self):
        old_val = os.environ.get("DEINEI_ACCESS_TOKEN")
        try:
            os.environ["DEINEI_ACCESS_TOKEN"] = "custom_super_secret_token"
            t = generate_token()
            self.assertEqual(t, "custom_super_secret_token")
            self.assertTrue(verify_token("custom_super_secret_token", t))
        finally:
            if old_val is not None:
                os.environ["DEINEI_ACCESS_TOKEN"] = old_val
            else:
                os.environ.pop("DEINEI_ACCESS_TOKEN", None)

    def test_extract_cookie(self):
        cookie_header = "foo=bar; deinei_token=secret_val_123; other=456"
        extracted = extract_token_from_cookie(cookie_header)
        self.assertEqual(extracted, "secret_val_123")

        self.assertIsNone(extract_token_from_cookie(None))
        self.assertIsNone(extract_token_from_cookie(""))
        self.assertIsNone(extract_token_from_cookie("foo=bar; baz=1"))

    def test_extract_auth_header(self):
        self.assertEqual(extract_token_from_auth_header("Bearer my_token"), "my_token")
        self.assertEqual(extract_token_from_auth_header("bearer my_token"), "my_token")
        self.assertIsNone(extract_token_from_auth_header("Basic dXNlcjpwYXNz"))
        self.assertIsNone(extract_token_from_auth_header(None))

    def test_extract_query(self):
        self.assertEqual(extract_token_from_query("token=my_query_token&foo=bar"), "my_query_token")
        self.assertEqual(extract_token_from_query("foo=bar&token=abc123_xyz"), "abc123_xyz")
        self.assertIsNone(extract_token_from_query("foo=bar"))
        self.assertIsNone(extract_token_from_query(""))
        self.assertIsNone(extract_token_from_query(None))

    def test_make_auth_cookie(self):
        cookie = make_auth_cookie("test_token_abc", max_age_seconds=3600, secure=False)
        self.assertIn("deinei_token=test_token_abc", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Path=/", cookie)
        self.assertIn("Max-Age=3600", cookie)
        self.assertNotIn("Secure", cookie)

        secure_cookie = make_auth_cookie("test_token_abc", max_age_seconds=3600, secure=True)
        self.assertIn("Secure", secure_cookie)

    def test_sanitize_url_for_log(self):
        url = "http://192.168.1.10:8090/?token=very_secret_token&user=test"
        sanitized = sanitize_url_for_log(url)
        self.assertNotIn("very_secret_token", sanitized)
        self.assertIn("token=%5BREDACTED%5D", sanitized)
        self.assertIn("user=test", sanitized)

        path_only = "/v1/chat?token=secret123"
        sanitized_path = sanitize_url_for_log(path_only)
        self.assertNotIn("secret123", sanitized_path)
        self.assertIn("token=%5BREDACTED%5D", sanitized_path)

        no_token = "/v1/chat?session=abc"
        self.assertEqual(sanitize_url_for_log(no_token), no_token)

    def test_strip_token_from_url(self):
        self.assertEqual(strip_token_from_url("/?token=abc"), "/")
        self.assertEqual(strip_token_from_url("/chat?token=abc&foo=1"), "/chat?foo=1")
        self.assertEqual(strip_token_from_url("/chat?bar=2&token=abc"), "/chat?bar=2")
        self.assertEqual(strip_token_from_url("/chat"), "/chat")


if __name__ == "__main__":
    unittest.main()
