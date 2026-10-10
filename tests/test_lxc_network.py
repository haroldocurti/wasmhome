import pathlib
import socket
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from probe_lxc_network import handle_connection, response, serve, validate_response

NONCE = b"a" * 64
REQUEST = b"GET /wasmhome-lxc/" + NONCE + b" HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n"


class LxcNetworkTests(unittest.TestCase):
    def test_nonconcrete_endpoints_fail_before_socket_creation(self):
        with mock.patch("probe_lxc_network.socket.socket") as factory:
            for invalid in ("0.0.0.0", "224.0.0.1", "255.255.255.255", "https://192.0.2.1"):
                for bind, expected in ((invalid, "192.0.2.2"), ("192.0.2.1", invalid)):
                    with self.subTest(bind=bind, expected=expected):
                        with self.assertRaises(ValueError):
                            serve(bind, expected)
            factory.assert_not_called()

    def exchange(self, request, expected="127.0.0.1", timeout=0.15):
        records = []
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            with socket.create_connection(listener.getsockname(), timeout=1) as client:
                connection, address = listener.accept()
                worker = threading.Thread(target=lambda: records.append(
                    handle_connection(connection, address, expected, timeout)))
                worker.start()
                if request:
                    client.sendall(request)
                received = bytearray()
                while True:
                    try:
                        data = client.recv(4096)
                    except ConnectionResetError:
                        break
                    if not data:
                        break
                    received.extend(data)
                worker.join(timeout=1)
                self.assertFalse(worker.is_alive())
        return bytes(received), records[0]

    def test_real_tcp_round_trip_records_nonce_and_peer(self):
        received, record = self.exchange(REQUEST)
        self.assertTrue(validate_response(received, NONCE, "127.0.0.1"))
        self.assertEqual(record["result"], "response_sent")
        self.assertEqual(record["nonce"], NONCE.decode())
        self.assertEqual(record["source_ipv4"], "127.0.0.1")

    def test_wrong_source_is_rejected_without_echo(self):
        received, record = self.exchange(REQUEST, expected="192.0.2.1")
        self.assertEqual(record["result"], "rejected_source")
        self.assertNotIn(NONCE, received)
        self.assertFalse(validate_response(received, NONCE, "127.0.0.1"))

    def test_malformed_nonce_and_arbitrary_path_are_rejected(self):
        for request in (REQUEST.replace(NONCE, b"z" * 64), REQUEST.replace(b"wasmhome-lxc", b"other"),
                        REQUEST.replace(NONCE, b"a" * 63), REQUEST + b"extra"):
            with self.subTest(request=request):
                received, record = self.exchange(request)
                self.assertEqual(record["result"], "rejected_request")
                self.assertFalse(validate_response(received, NONCE, "127.0.0.1"))

    def test_silent_peer_and_incomplete_request_have_total_deadline(self):
        for request in (None, b"GET /wasmhome-lxc/"):
            with self.subTest(request=request):
                started = time.monotonic()
                _, record = self.exchange(request)
                self.assertEqual(record["result"], "timeout")
                self.assertLess(time.monotonic() - started, 0.8)

    def test_oversized_request_is_rejected(self):
        received, record = self.exchange(b"G" * 1025)
        self.assertEqual(record["result"], "oversized_request")
        self.assertFalse(validate_response(received, NONCE, "127.0.0.1"))

    def test_response_requires_matching_nonce_source_and_server_contract(self):
        good = response(NONCE, "127.0.0.1")
        for bad in (good.replace(NONCE, b"b" * 64), good.replace(b"127.0.0.1", b"127.0.0.2"),
                    good.replace(b"200 OK", b"503 No"), good + b"extra", good[:-1]):
            with self.subTest(bad=bad):
                self.assertFalse(validate_response(bad, NONCE, "127.0.0.1"))


if __name__ == "__main__":
    unittest.main()
