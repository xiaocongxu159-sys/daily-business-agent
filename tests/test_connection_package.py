from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from agent.lingxing_connection_package import ConnectionPackageError, parse_connection_package
from agent.lingxing_integration import create_integrated_app
from agent.lingxing_secure_store import LingxingCredentials, TestOnlyProtector
from agent.lingxing_tls_proxy import parse_tls_proxy_url
from agent.settings import AgentSettings

TOKEN = "local-package-test-token"
PACKAGE_NAME = "DailyBusinessAgent-Connection.dba"
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


def bundle_text(*, port: int = 18443, payload_schema: str = "daily-business-agent.relay-connection.v1"):
    payload = {
        "schema": payload_schema,
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
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    bundle = {
        "schema": "daily-business-agent.connection-bundle.v1",
        "payload": payload,
        "integrity": {
            "algorithm": "sha256",
            "payload_sha256": hashlib.sha256(canonical).hexdigest(),
        },
    }
    return json.dumps(bundle, ensure_ascii=False, indent=2) + "\n"


class ConnectionPackageTests(unittest.TestCase):
    def test_valid_single_file_builds_pinned_tls_url_without_secret_repr(self):
        connection = parse_connection_package(
            package_name=PACKAGE_NAME,
            package_text=bundle_text(),
        )
        target = parse_tls_proxy_url(connection.proxy_url())
        self.assertEqual(target.host, "relay.example.test")
        self.assertEqual(target.port, 18443)
        self.assertEqual(target.password, PASSWORD)
        self.assertNotIn(PASSWORD, repr(connection))
        self.assertNotIn(FINGERPRINT, repr(connection))

    def test_embedded_integrity_and_schema_fail_closed(self):
        tampered = json.loads(bundle_text())
        tampered["payload"]["relay"]["port"] = 9443
        with self.assertRaisesRegex(ConnectionPackageError, "完整性校验失败"):
            parse_connection_package(
                package_name=PACKAGE_NAME,
                package_text=json.dumps(tampered),
            )

        unsupported = json.loads(bundle_text())
        unsupported["payload"]["schema"] = "unsupported"
        payload = unsupported["payload"]
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        unsupported["integrity"]["payload_sha256"] = hashlib.sha256(canonical).hexdigest()
        with self.assertRaisesRegex(ConnectionPackageError, "版本不受支持"):
            parse_connection_package(
                package_name=PACKAGE_NAME,
                package_text=json.dumps(unsupported),
            )

    def _app(self, root: Path):
        settings = AgentSettings(
            data_root=root / "data",
            host="127.0.0.1",
            port=8766,
            allowed_origins=(),
        )
        return create_integrated_app(
            settings,
            token=TOKEN,
            provider_factory=FakeProvider,
            protector=TestOnlyProtector(),
            start_service=False,
        )

    def test_native_stage_encrypts_saves_and_deletes_source_file(self):
        FakeProvider.seen.clear()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / PACKAGE_NAME
            source.write_text(bundle_text(), encoding="utf-8")
            app = self._app(root)

            with TestClient(app) as client:
                page = client.get("/lingxing")
                self.assertEqual(page.status_code, 200)
                self.assertIn('id="package-ready"', page.text)
                self.assertNotIn('type="file"', page.text)
                self.assertNotIn(".sha256", page.text)
                self.assertNotIn('id="proxy-url"', page.text)

                staged = client.post(
                    "/v1/lingxing/stage-package",
                    json={"source_path": str(source.resolve())},
                    headers={"X-Agent-Token": TOKEN},
                )
                self.assertEqual(staged.status_code, 200, staged.text)
                import_token = staged.json()["import_token"]
                self.assertTrue(source.exists())

                payload = {
                    "app_id": "1234567890ABCDEF",
                    "app_secret": "app-secret-never-echo",
                    "import_token": import_token,
                    "auto_sync": True,
                    "sync_interval_minutes": 120,
                }
                response = client.post(
                    "/v1/lingxing/configure-package",
                    json=payload,
                    headers={"X-Agent-Token": TOKEN},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["connection_package_imported"])
                self.assertTrue(response.json()["source_deleted"])
                self.assertFalse(source.exists())
                for secret in (PASSWORD, FINGERPRINT, payload["app_secret"]):
                    self.assertNotIn(secret, response.text)

            store = app.state.lingxing_store
            encrypted = store.credentials_path.read_bytes()
            state = store.state_path.read_text(encoding="utf-8")
            for secret in (PASSWORD, FINGERPRINT, payload["app_secret"]):
                self.assertNotIn(secret.encode(), encrypted)
                self.assertNotIn(secret, state)

    def test_changed_source_is_not_deleted_after_successful_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / PACKAGE_NAME
            source.write_text(bundle_text(), encoding="utf-8")
            app = self._app(root)
            with TestClient(app) as client:
                staged = client.post(
                    "/v1/lingxing/stage-package",
                    json={"source_path": str(source.resolve())},
                    headers={"X-Agent-Token": TOKEN},
                )
                self.assertEqual(staged.status_code, 200, staged.text)
                source.write_text(bundle_text() + "\n", encoding="utf-8")
                response = client.post(
                    "/v1/lingxing/configure-package",
                    json={
                        "app_id": "1234567890ABCDEF",
                        "app_secret": "app-secret-never-echo",
                        "import_token": staged.json()["import_token"],
                        "auto_sync": True,
                        "sync_interval_minutes": 120,
                    },
                    headers={"X-Agent-Token": TOKEN},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertFalse(response.json()["source_deleted"])
                self.assertTrue(source.exists())


if __name__ == "__main__":
    unittest.main()
