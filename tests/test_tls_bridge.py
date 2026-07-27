from __future__ import annotations

import base64
import hashlib
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from agent.lingxing_tls_proxy import TlsProxyBridge, validate_secure_proxy_url


def generate_test_certificate(root: Path) -> tuple[Path, Path]:
    openssl = shutil.which("openssl")
    if not openssl:
        raise unittest.SkipTest("openssl unavailable")
    cert_path = root / "relay-cert.pem"
    key_path = root / "relay-key.pem"
    subprocess.run(
        [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", "/CN=test-relay", "-keyout", str(key_path), "-out", str(cert_path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=20,
    )
    return cert_path, key_path


class Relay:
    def __init__(self, cert: Path, key: Path):
        self.cert, self.key = cert, key
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.raw = b""
        self.decrypted = b""
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def serve(self):
        connection, _ = self.listener.accept()
        connection.settimeout(5)
        self.raw = connection.recv(4096, socket.MSG_PEEK)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, self.key)
        with context.wrap_socket(connection, server_side=True) as secured:
            self.decrypted = secured.recv(4096)
            secured.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")

    def close(self):
        self.listener.close()
        self.thread.join(timeout=5)


class TlsBridgeTests(unittest.TestCase):
    def test_remote_plain_proxy_rejected(self):
        with self.assertRaisesRegex(ValueError, r"tls\+http"):
            validate_secure_proxy_url("http://user:password@203.0.113.10:3128")

    def test_credentials_cross_socket_only_inside_tls(self):
        with tempfile.TemporaryDirectory() as tmp:
            cert, key = generate_test_certificate(Path(tmp))
            relay = Relay(cert, key)
            relay.thread.start()
            der = ssl.PEM_cert_to_DER_cert(cert.read_text())
            fingerprint = hashlib.sha256(der).hexdigest()
            proxy_url = (
                f"tls+http://relay-user:relay-password@127.0.0.1:{relay.port}"
                f"?sha256={fingerprint}&server_name=test-relay"
            )
            authorization = base64.b64encode(b"relay-user:relay-password").decode()
            request = (
                "CONNECT openapi.lingxing.com:443 HTTP/1.1\r\n"
                "Host: openapi.lingxing.com:443\r\n"
                f"Proxy-Authorization: Basic {authorization}\r\n\r\n"
            ).encode()
            try:
                with TlsProxyBridge(proxy_url) as bridge:
                    local_port = int(bridge.local_proxy_url.rsplit(":", 1)[1])
                    with socket.create_connection(("127.0.0.1", local_port), timeout=5) as client:
                        client.sendall(request)
                        response = client.recv(4096)
                    self.assertIn(b"200 Connection established", response)
            finally:
                relay.close()
            self.assertTrue(relay.raw.startswith(b"\x16\x03"))
            self.assertNotIn(b"CONNECT", relay.raw)
            self.assertNotIn(b"relay-password", relay.raw)
            self.assertEqual(relay.decrypted, request)


if __name__ == "__main__":
    unittest.main()
