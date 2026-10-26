"""
LAN セキュリティ & プライベートIP判定モジュール (純粋関数)
------------------------------------------------------------
DNS Rebinding および CSRF 攻撃を防止しつつ、同一LAN内 (プライベートIP)
からの安全なアクセスを判定・許可するためのユーティリティ関数群。
"""

import ipaddress
from urllib.parse import urlparse

def is_trusted_private_or_loopback(host_or_ip: str, allow_lan: bool = False) -> bool:
    """ループバック (127.0.0.1/localhost/::1) または RFC 1918 プライベートIPアドレスか検査"""
    if not host_or_ip:
        return False

    clean = str(host_or_ip).strip()
    # IPv6 の角括弧 "[::1]" や "[::1]:8080" の除去
    if clean.startswith('[') and ']' in clean:
        clean = clean[1:clean.index(']')]
    elif ':' in clean and clean.count(':') == 1:
        # IPv4のポート付き "192.168.1.1:8080"
        clean = clean.split(':')[0]
    # clean.count(':') > 1 の場合はプレーンなIPv6アドレス (例: "::1")

    h = clean.lower()
    if h in ('localhost', '127.0.0.1', '::1'):
        return True

    if not allow_lan:
        return False

    try:
        ip = ipaddress.ip_address(h)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False

def is_origin_allowed(origin_str: str, allowed_origins: set, allow_lan: bool = False) -> bool:
    """Origin または Referer が許可リストまたは信頼されたプライベートIPか検査"""
    if not origin_str:
        return False
    clean = str(origin_str).rstrip('/')
    if clean in allowed_origins:
        return True
    if allow_lan:
        try:
            p = urlparse(origin_str)
            if p.scheme in ('http', 'https') and is_trusted_private_or_loopback(p.hostname or '', allow_lan=True):
                return True
        except Exception:
            pass
    return False
