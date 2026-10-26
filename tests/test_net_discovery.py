"""
泥濘 (Deinei) net_discovery 単体テスト
"""

import os
import sys
import unittest

# backend/data をインポートパスに追加
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(REPO_ROOT, "backend", "data")
if DATA_DIR not in sys.path:
    sys.path.insert(0, DATA_DIR)

from net_discovery import (
    is_excluded_ip,
    is_private_ip,
    get_ip_priority,
    get_lan_ip_candidates,
    get_best_lan_ip,
    get_network_profiles,
    get_tailscale_info,
    VIRTUAL_ADAPTER_PATTERN,
)


class TestNetDiscovery(unittest.TestCase):
    def test_is_excluded_ip(self):
        # ループバック
        self.assertTrue(is_excluded_ip("127.0.0.1"))
        self.assertTrue(is_excluded_ip("127.0.1.1"))
        # APIPA / リンクローカル
        self.assertTrue(is_excluded_ip("169.254.10.20"))
        # Tailscale 等の CGNAT
        self.assertTrue(is_excluded_ip("100.64.0.1"))
        self.assertTrue(is_excluded_ip("100.100.50.2"))
        # マルチキャスト / 特殊
        self.assertTrue(is_excluded_ip("224.0.0.1"))
        self.assertTrue(is_excluded_ip("255.255.255.255"))
        # 不正な文字列
        self.assertTrue(is_excluded_ip("invalid-ip"))
        # 正常なプライベートIPは除外されない
        self.assertFalse(is_excluded_ip("192.168.1.100"))
        self.assertFalse(is_excluded_ip("10.0.1.50"))
        self.assertFalse(is_excluded_ip("172.20.1.5"))

    def test_is_private_ip(self):
        self.assertTrue(is_private_ip("192.168.1.1"))
        self.assertTrue(is_private_ip("10.10.10.10"))
        self.assertTrue(is_private_ip("172.16.0.1"))
        self.assertTrue(is_private_ip("172.31.255.254"))

        # パブリックIP
        self.assertFalse(is_private_ip("8.8.8.8"))
        self.assertFalse(is_private_ip("1.1.1.1"))
        # ループバックやリンクローカル
        self.assertFalse(is_private_ip("127.0.0.1"))
        self.assertFalse(is_private_ip("169.254.1.1"))

    def test_get_ip_priority(self):
        self.assertEqual(get_ip_priority("192.168.1.50"), 1)
        self.assertEqual(get_ip_priority("10.0.0.5"), 2)
        self.assertEqual(get_ip_priority("172.20.0.1"), 3)
        self.assertEqual(get_ip_priority("172.31.0.1"), 3)
        self.assertEqual(get_ip_priority("172.32.0.1"), 4)

    def test_virtual_adapter_pattern(self):
        self.assertTrue(bool(VIRTUAL_ADAPTER_PATTERN.search("vEthernet (WSL)")))
        self.assertTrue(bool(VIRTUAL_ADAPTER_PATTERN.search("Docker Desktop")))
        self.assertTrue(bool(VIRTUAL_ADAPTER_PATTERN.search("Tailscale")))
        self.assertTrue(bool(VIRTUAL_ADAPTER_PATTERN.search("VirtualBox Host-Only Ethernet Adapter")))
        self.assertTrue(bool(VIRTUAL_ADAPTER_PATTERN.search("VMware Network Adapter VMnet1")))
        self.assertTrue(bool(VIRTUAL_ADAPTER_PATTERN.search("Bluetooth ネットワーク接続")))
        self.assertFalse(bool(VIRTUAL_ADAPTER_PATTERN.search("Wi-Fi")))
        self.assertFalse(bool(VIRTUAL_ADAPTER_PATTERN.search("イーサネット")))

    def test_get_lan_ip_candidates(self):
        cands = get_lan_ip_candidates()
        self.assertIsInstance(cands, list)
        for c in cands:
            self.assertIn("ip", c)
            self.assertIn("interface", c)
            self.assertIn("priority", c)
            # 除外対象IPが含まれていないことを確認
            self.assertFalse(is_excluded_ip(c["ip"]))
            self.assertTrue(is_private_ip(c["ip"]))

    def test_get_network_profiles(self):
        profiles = get_network_profiles()
        self.assertIsInstance(profiles, list)
        for p in profiles:
            self.assertIn("name", p)
            self.assertIn("interface", p)
            self.assertIn("category", p)

    def test_get_tailscale_info(self):
        ts = get_tailscale_info()
        self.assertIsInstance(ts, dict)
        self.assertIn("installed", ts)
        self.assertIn("running", ts)
        self.assertIn("backend_state", ts)
        self.assertIn("ip", ts)
        self.assertIn("dns_name", ts)

    def test_tailscale_helpers_signature(self):
        from net_discovery import download_and_install_tailscale, trigger_tailscale_login
        self.assertTrue(callable(download_and_install_tailscale))
        self.assertTrue(callable(trigger_tailscale_login))


if __name__ == "__main__":
    unittest.main()
