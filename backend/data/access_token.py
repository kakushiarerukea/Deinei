"""
泥濘 (Deinei) 認証トークン管理モジュール (純粋関数)
--------------------------------------------------------------------------------
ゲートウェイアクセス認証用のトークン生成、Cookie操作、定数時間比較、ログ用サニタイズを担当。
外部フレームワークに依存せず Python 標準ライブラリのみで構成。
"""

import hmac
import os
import secrets
import urllib.parse
from http import cookies
from typing import Optional, Tuple


COOKIE_NAME = "deinei_token"


def generate_token() -> str:
    """
    暗号学的に安全なランダムトークンを生成する。
    環境変数 DEINEI_ACCESS_TOKEN が指定されていればそれを最優先で使用する。
    """
    env_token = os.environ.get("DEINEI_ACCESS_TOKEN", "").strip()
    if env_token:
        return env_token
    return secrets.token_urlsafe(32)


def verify_token(expected_token: str, candidate_token: Optional[str]) -> bool:
    """
    期待されるトークンと検証対象トークンを定数時間比較 (hmac.compare_digest) で照合する。
    タイミング攻撃によるトークン推測を防止する。
    """
    if not expected_token or not candidate_token:
        return False
    if not isinstance(expected_token, str) or not isinstance(candidate_token, str):
        return False
    return hmac.compare_digest(expected_token, candidate_token)


def extract_token_from_cookie(cookie_header: Optional[str]) -> Optional[str]:
    """
    Cookie ヘッダー文字列から deinei_token の値を抽出する。
    パースエラーや存在しない場合は None を返す。
    """
    if not cookie_header:
        return None
    try:
        c = cookies.SimpleCookie()
        c.load(cookie_header)
        morsel = c.get(COOKIE_NAME)
        if morsel:
            return morsel.value
    except Exception:
        return None
    return None


def extract_token_from_auth_header(auth_header: Optional[str]) -> Optional[str]:
    """
    Authorization: Bearer <token> ヘッダーからトークンを抽出する。
    """
    if not auth_header:
        return None
    parts = auth_header.strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def extract_token_from_query(query_string: Optional[str]) -> Optional[str]:
    """
    クエリ文字列 (例: 'token=xyz&foo=bar') から token パラメータの値を抽出する。
    """
    if not query_string:
        return None
    try:
        params = urllib.parse.parse_qs(query_string, keep_blank_values=False)
        tokens = params.get("token")
        if tokens and len(tokens) > 0:
            return tokens[0]
    except Exception:
        return None
    return None


def make_auth_cookie(token: str, max_age_seconds: int = 30 * 86400, secure: bool = False) -> str:
    """
    認証用 Set-Cookie ヘッダー文字列を生成する。
    HttpOnly, SameSite=Lax, Path=/ を付与。
    HTTPS環境では secure=True を指定して Secure 属性を付与する。
    """
    c = cookies.SimpleCookie()
    c[COOKIE_NAME] = token
    morsel = c[COOKIE_NAME]
    morsel["path"] = "/"
    morsel["httponly"] = True
    morsel["samesite"] = "Lax"
    morsel["max-age"] = str(max_age_seconds)
    if secure:
        morsel["secure"] = True
    # Output() returns string formatted like 'Set-Cookie: deinei_token=...; ...'
    # We strip 'Set-Cookie: ' to return just the header value.
    output_str = morsel.OutputString()
    return output_str


def sanitize_url_for_log(raw_url: str) -> str:
    """
    アクセスログやコンソール表示用に、URL内の token パラメータをマスクする。
    例: '/?token=secret123&v=1' -> '/?token=[REDACTED]&v=1'
    """
    if not raw_url or "token" not in raw_url:
        return raw_url
    try:
        parts = urllib.parse.urlsplit(raw_url)
        if not parts.query:
            return raw_url
        query_pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        sanitized_pairs = []
        for k, v in query_pairs:
            if k.lower() == "token":
                sanitized_pairs.append((k, "[REDACTED]"))
            else:
                sanitized_pairs.append((k, v))
        new_query = urllib.parse.urlencode(sanitized_pairs)
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))
    except Exception:
        return raw_url


def strip_token_from_url(raw_path_and_query: str) -> str:
    """
    302 リダイレクト用に、URLまたはパス文字列から token パラメータを完全除去する。
    ブラウザのURLバーやブックマークにトークンが残存するのを防ぐ。
    例: '/?token=abc' -> '/'
    例: '/chat?token=abc&session=1' -> '/chat?session=1'
    """
    if not raw_path_and_query:
        return "/"
    try:
        parts = urllib.parse.urlsplit(raw_path_and_query)
        if not parts.query:
            return raw_path_and_query
        query_pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        remaining_pairs = [(k, v) for k, v in query_pairs if k.lower() != "token"]
        new_query = urllib.parse.urlencode(remaining_pairs)
        path = parts.path or "/"
        if new_query:
            return f"{path}?{new_query}"
        return path
    except Exception:
        return raw_path_and_query
