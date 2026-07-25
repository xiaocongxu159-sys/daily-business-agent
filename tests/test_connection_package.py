from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from agent.lingxing_connection_package import ConnectionPackageError, parse_connection_package_pair
from agent.lingxing_integration import create_integrated_app
from agent.lingxing_secure_store import LingxingCredentials, TestOnlyProtector
from agent.lingxing_tls_proxy import parse_tls_proxy_url
from agent.settings import AgentSettings

TOKEN = "local-package-test-token"
PACKAGE_NAME = "DailyBusinessAgent-Server-Connection.dba-connection.json"
PASSWORD = "package-secret-" + "x" * 40
FINGERPRINT = "ab" * 32
SHOPS = [{
    "mid": 1,
    "sid": 2,
    "seller_id": "SELLER-PACKAGE",
    "seller_name": "连接包测试店铺",
    "account_id": 3,
    "account_name": "测试账号",
    "marketplace_id": "ATVPDKIKX0DER",
    "region": "NA",
    "country": "US",
    "status": 1,
    "ads_authorized": False,
}]


class FakeProvider:
    seen: list[LingxingCredentials] = []

    def list_shops(self, credentials: LingxingCredentials) -> list[dict]:
        self.seen.append(credentials)
        return SHOPS


def package_pair(*, port: int = 18443, schema: str = "daily-business-agent.relay-connection.v1"):
    package = {
        "schema": schema,
        "package_id": "0123456789abcdef0123456789abcdef",
        "created_at": "2026-07-25T10:00:00+00:00",
        "transport": "pinned_outer_tls",
        "relay": {
            "scheme": "tls+http",
            "host": "relay.example.test",
            "port": port,
            "server_name": "relay.example.test",
            "username": "relay_user_1234",
            "password": PASSWORD,
            "certificate_sha256": FINGERPRINT,
        },
        "contains": {
            "lingxing_app_id": False,
            "lingxing_app_secret": False,
            "tls_private_key": False,
            "business_data": False,
        },
    }
    text = json.dumps(package, ensure_ascii=False, indent=2) + "\n"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text, PACKAGE_NAME + ".sha256", f"{digest}  {PACKAGE_NAME}\n"


class ConnectionPackageTests(unittest.TestCase):
    def test_valid_pair_builds_pinned_tls_url_without_secret_repr(self):
        package_text, checksum_name, checksum_text = package_pair()
        connection = parse_connection_package_pair(
            package_name=PACKAGE_NAME,
            package_text=package_text,
            checksum_name=checksum_name,
            checksum_text=checksum_text,
        )
        target = parse_tls_proxy_url(connection.proxy_url())
        self.assertEqual(target.host, "relay.example.test")
        self.assertEqual(target.port, 18443)
        self.assertEqual(target.password, PASSWORD)
        self.assertNotIn(PASSWORD, repr(connection))
        self.assertNotIn(FINGERPRINT, repr(connection))

    def test_checksum_and_schema_fail_closed(self):
        package_text, checksum_name, checksum_text = package_pair()
        with self.assertRaisesRegex(ConnectionPackageError, "SHA256 校验失败"):
            parse_connection_package_pair(
                package_name=PACKAGE_NAME,
                package_text=package_text + " ",
                checksum_name=checksum_name,
                checksum_text=checksum_text,
            )
        unsupported, _, _ = package_pair(schema="unsupported")
        digest = hashlib.sha256(unsupported.encode()).hexdigest()
        with self.assertRaisesRegex(ConnectionPackageError, "版本不受支持"):
            parse_connection_package_pair(
                package_name=PACKAGE_NAME,
                package_text=unsupported,
                checksum_name=PACKAGE_NAME + ".sha256",
                checksum_text=f"{digest}  {PACKAGE_NAME}\n",
            )

    def test_endpoint_encrypts_and_never_echoes_secrets(self):
        FakeProvider.seen.clear()
        with tempfile.TemporaryDirectory() as tmp:
            settings = AgentSettings(
                data_root=Path(tmp), host="127.0.0.1", port=8766, allowed_origins=()
            )
            app = create_integrated_app(
                settings,
                token=TOKEN,
                provider_factory=FakeProvider,
                protector=TestOnlyProtector(),
                start_service=False,
            )
            package_text, checksum_name, checksum_text = package_pair()
            payload = {
                "app_id": "1234567890ABCDEF",
                "app_secret": "app-secret-never-echo",
                "package_name": PACKAGE_NAME,
                "package_text": package_text,
                "checksum_name": checksum_name,
                "checksum_text": checksum_text,
                "auto_sync": True,
                "sync_interval_minutes": 120,
            }
            with TestClient(app) as client:
                page = client.get("/lingxing")
                self.assertEqual(page.status_code, 200)
                self.assertIn('id="connection-package"', page.text)
                self.assertNotIn('id="proxy-url"', page.text)
                response = client.post(
                    "/v1/lingxing/configure-package",
                    json=payload,
                    headers={"X-Agent-Token": TOKEN},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["connection_package_imported"])
                for secret in (PASSWORD, FINGERPRINT, payload["app_secret"]):
                    self.assertNotIn(secret, response.text)

            store = app.state.lingxing_store
            encrypted = store.credentials_path.read_bytes()
            state = store.state_path.read_text(encoding="utf-8")
            for secret in (PASSWORD, FINGERPRINT, payload["app_secret"]):
                self.assertNotIn(secret.encode(), encrypted)
                self.assertNotIn(secret, state)


if __name__ == "__main__":
    unittest.main()
