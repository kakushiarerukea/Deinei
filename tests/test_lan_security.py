import unittest
import sys
import os

# Pure function test without importing server.py
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend', 'data')))
from lan_security import is_trusted_private_or_loopback, is_origin_allowed

class TestLanSecurity(unittest.TestCase):
    def test_private_ip_loopback(self):
        # 127.0.0.1, localhost, and ::1 should always be trusted regardless of allow_lan
        self.assertTrue(is_trusted_private_or_loopback('127.0.0.1', allow_lan=False))
        self.assertTrue(is_trusted_private_or_loopback('localhost', allow_lan=False))
        self.assertTrue(is_trusted_private_or_loopback('::1', allow_lan=False))
        self.assertTrue(is_trusted_private_or_loopback('[::1]:8080', allow_lan=False))
        self.assertTrue(is_trusted_private_or_loopback('127.0.0.1:5000', allow_lan=False))

    def test_public_ip_and_external_domain_rejected(self):
        # Public IP or external domains should never be trusted even when allow_lan=True
        self.assertFalse(is_trusted_private_or_loopback('8.8.8.8', allow_lan=True))
        self.assertFalse(is_trusted_private_or_loopback('1.1.1.1:80', allow_lan=True))
        self.assertFalse(is_trusted_private_or_loopback('evil.com', allow_lan=True))
        self.assertFalse(is_trusted_private_or_loopback('evil.com:8080', allow_lan=True))
        self.assertFalse(is_trusted_private_or_loopback('192.168.1.1.evil.com', allow_lan=True))
        self.assertFalse(is_trusted_private_or_loopback('', allow_lan=True))
        self.assertFalse(is_trusted_private_or_loopback(None, allow_lan=True))

    def test_private_ip_allowed_when_allow_lan(self):
        self.assertTrue(is_trusted_private_or_loopback('192.168.1.1', allow_lan=True))
        self.assertTrue(is_trusted_private_or_loopback('192.168.11.3:8080', allow_lan=True))
        self.assertTrue(is_trusted_private_or_loopback('10.0.0.1', allow_lan=True))
        self.assertTrue(is_trusted_private_or_loopback('10.0.0.1:5001', allow_lan=True))
        self.assertTrue(is_trusted_private_or_loopback('172.16.0.1', allow_lan=True))
        self.assertTrue(is_trusted_private_or_loopback('172.31.255.255', allow_lan=True))

    def test_private_ip_rejected_when_not_allow_lan(self):
        self.assertFalse(is_trusted_private_or_loopback('192.168.1.1', allow_lan=False))
        self.assertFalse(is_trusted_private_or_loopback('192.168.11.3:8080', allow_lan=False))
        self.assertFalse(is_trusted_private_or_loopback('10.0.0.1', allow_lan=False))
        self.assertFalse(is_trusted_private_or_loopback('172.16.0.1', allow_lan=False))

    def test_origin_allowed(self):
        allowed = {'http://127.0.0.1:8080', 'http://localhost:8080'}
        # Default allowed origins
        self.assertTrue(is_origin_allowed('http://127.0.0.1:8080', allowed, allow_lan=False))
        self.assertTrue(is_origin_allowed('http://localhost:8080/', allowed, allow_lan=False))
        
        # When allow_lan is True
        self.assertTrue(is_origin_allowed('http://192.168.11.3:8080', allowed, allow_lan=True))
        self.assertTrue(is_origin_allowed('http://10.0.0.5:5000', allowed, allow_lan=True))
        self.assertFalse(is_origin_allowed('http://evil.com', allowed, allow_lan=True))
        self.assertFalse(is_origin_allowed('http://8.8.8.8:8080', allowed, allow_lan=True))
        self.assertFalse(is_origin_allowed('null', allowed, allow_lan=True))
        self.assertFalse(is_origin_allowed('', allowed, allow_lan=True))

        # When allow_lan is False
        self.assertFalse(is_origin_allowed('http://192.168.11.3:8080', allowed, allow_lan=False))

if __name__ == '__main__':
    unittest.main()
