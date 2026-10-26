import sys
import unittest
import socket
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.data.gateway import find_available_port, DeineiGateway

class TestGatewayPortSlide(unittest.TestCase):
    def test_find_available_port_skips_busy_port(self):
        # テスト用にポートを1つ占有する
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        busy_port = s.getsockname()[1]

        try:
            # busy_port から探索を開始した場合、次のポートが返るはず
            free_port = find_available_port("127.0.0.1", busy_port, max_attempts=5)
            self.assertNotEqual(free_port, busy_port)
            self.assertGreater(free_port, busy_port)
        finally:
            s.close()

    def test_deinei_gateway_auto_slides_busy_port(self):
        # 8090 または任意のポートを占有
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        busy_port = s.getsockname()[1]

        try:
            gw = DeineiGateway(bind_host="127.0.0.1", port=busy_port)
            gw.start(blocking=False, max_port_attempts=5)
            try:
                # 占有されていたポートを避けて、次のポートで立ち上がっているか検証
                self.assertNotEqual(gw.port, busy_port)
                self.assertGreater(gw.port, busy_port)
            finally:
                gw.stop()
        finally:
            s.close()

    def test_deinei_net_imports_find_available_port(self):
        import backend.data.deinei_net as dn
        self.assertTrue(hasattr(dn, 'find_available_port'))
        self.assertTrue(callable(dn.find_available_port))

    def test_deinei_net_run_serve_lan_mode_no_unbound_error(self):
        """deinei_net.run_serve() で bind_host のスコープや未定義エラーが発生せず正常起動できるか検証"""
        import backend.data.deinei_net as dn

        mock_args = MagicMock()
        mock_args.lan = True
        mock_args.tailscale = False
        mock_args.port = 8090
        mock_args.token = "test_token"
        mock_args.no_qr = True
        mock_args.llama_port = 8080
        mock_args.flask_port = 5000
        mock_args.tts_port = 5001

        with patch.object(dn, "get_lan_ip_candidates", return_value=[{"ip": "192.168.1.100", "interface": "Wi-Fi"}]), \
             patch.object(dn, "DeineiGateway") as mock_gw_class:
            mock_gw = MagicMock()
            mock_gw_class.return_value = mock_gw

            dn.run_serve(mock_args)
            mock_gw.start.assert_called_once_with(blocking=True)
            mock_gw.stop.assert_called_once()

    def test_deinei_net_run_serve_tailscale_mode_no_unbound_error(self):
        """Tailscale モードでも bind_host や URL 生成が正常に行われるか検証"""
        import backend.data.deinei_net as dn

        mock_args = MagicMock()
        mock_args.lan = False
        mock_args.tailscale = True
        mock_args.https = False
        mock_args.port = 8090
        mock_args.token = "test_token"
        mock_args.no_qr = True
        mock_args.llama_port = 8080
        mock_args.flask_port = 5000
        mock_args.tts_port = 5001

        with patch.object(dn, "get_tailscale_info", return_value={"installed": True, "running": True, "ip": "100.100.100.100", "dns_name": "my-pc.tailscale.net"}), \
             patch.object(dn, "find_available_port", return_value=8090) as mock_find_port, \
             patch.object(dn, "DeineiGateway") as mock_gw_class:
            mock_gw = MagicMock()
            mock_gw_class.return_value = mock_gw

            dn.run_serve(mock_args)
            mock_find_port.assert_called_once_with("100.100.100.100", 8090)
            mock_gw.start.assert_called_once_with(blocking=True)
            mock_gw.stop.assert_called_once()

    def test_find_available_port_exhaustion_raises_runtime_error(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('127.0.0.1', 0))
        busy_port = s.getsockname()[1]
        try:
            with self.assertRaises(RuntimeError):
                find_available_port('127.0.0.1', busy_port, max_attempts=1)
        finally:
            s.close()

if __name__ == '__main__':
    unittest.main()
