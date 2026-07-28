import json
import time

import pytest
from fastapi.testclient import TestClient

from agent.lingxing_integration import create_integrated_app
from agent.lingxing_probe import (
    LingxingProbeResultStore,
    LingxingReadOnlyProbeService,
    ProbeError,
    classify_probe_error,
    summarize_response,
    validate_probe_result,
)
from agent.lingxing_secure_store import (
    LingxingCredentials,
    LingxingLocalStore,
    TestOnlyProtector,
)
from agent.settings import AgentSettings

TOKEN = "synthetic-probe-test-token"
SAME_ORIGIN = {
    "Origin": "http://127.0.0.1:8766",
    "Referer": "http://127.0.0.1:8766/lingxing",
    "Sec-Fetch-Site": "same-origin",
}


class FakeShopProvider:
    def list_shops(self, credentials):
        return [
            {
                "sid": 9001,
                "seller_id": "SELLER-SYNTHETIC-SECRET",
                "seller_name": "Synthetic Probe Store",
                "marketplace_id": "MARKETPLACE-SYNTHETIC-SECRET",
                "country": "US",
                "region": "NA",
                "status": "active",
                "ads_authorized": True,
            }
        ]


class FakeProbeProvider:
    def run_probe(self, credentials, cached_shops):
        assert credentials.app_id == "synthetic-probe-app"
        assert credentials.app_secret == "synthetic-probe-secret"
        assert cached_shops[0]["sid"] == 9001
        return {
            "status": "success",
            "datasets": [
                {
                    "dataset": "orders",
                    "status": "success",
                    "fields": [
                        "amazon_order_id",
                        "currency_code",
                        "msku",
                        "purchase_date_loc",
                        "sales_amt",
                        "sid",
                    ],
                    "sampled_rows": 1,
                    "response_count": 1,
                    "total_count": 37,
                    "date_from": "2026-07-27",
                    "date_to": "2026-07-27",
                    "error_code": "",
                },
                {
                    "dataset": "sales_traffic",
                    "status": "task_created",
                    "fields": ["report_id"],
                    "sampled_rows": 0,
                    "response_count": 0,
                    "total_count": None,
                    "date_from": "2026-07-27",
                    "date_to": "2026-07-27",
                    "error_code": "",
                },
            ],
            "shop_id": "DO-NOT-PERSIST-SHOP-ID",
            "raw_rows": [{"amazon_order_id": "DO-NOT-PERSIST-ORDER"}],
        }


def credentials():
    return LingxingCredentials(
        app_id="synthetic-probe-app",
        app_secret="synthetic-probe-secret",
        proxy_url=(
            "tls+http://synthetic-user:synthetic-pass@192.0.2.44:8443?fingerprint="
            + "ab" * 32
        ),
        auto_sync=False,
        sync_interval_minutes=120,
    )


def shops():
    return FakeShopProvider().list_shops(credentials())


def wait_for_probe(service, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = service.public_status()
        if not state["running"]:
            return state
        time.sleep(0.02)
    raise AssertionError("probe did not finish")


def test_response_summary_contains_schema_not_business_values():
    raw = {
        "response_count": 1,
        "total_count": 99,
        "data": [
            {
                "sid": 9001,
                "amazon_order_id": "ORDER-SYNTHETIC-SECRET",
                "msku": "MSKU-SYNTHETIC-SECRET",
                "sales_amt": 12345.67,
                "currency_code": "USD",
                "purchase_date_loc": "2026-07-27",
            }
        ],
        "request_id": "REQUEST-SYNTHETIC-SECRET",
    }
    summary = summarize_response(
        "orders", raw, date_fields=("purchase_date_loc",)
    )
    encoded = json.dumps(summary, ensure_ascii=False)
    assert summary["status"] == "success"
    assert summary["response_count"] == 1
    assert summary["total_count"] == 99
    assert summary["date_from"] == "2026-07-27"
    assert {"amazon_order_id", "sales_amt"}.issubset(summary["fields"])
    for forbidden in (
        "ORDER-SYNTHETIC-SECRET",
        "MSKU-SYNTHETIC-SECRET",
        "12345.67",
        "REQUEST-SYNTHETIC-SECRET",
    ):
        assert forbidden not in encoded


def test_probe_result_validation_discards_unknown_payload_and_rejects_bad_shape():
    safe = validate_probe_result(
        {
            "status": "success",
            "datasets": [
                {
                    "dataset": "listings",
                    "status": "no_data",
                    "fields": [],
                    "sampled_rows": 0,
                    "response_count": 0,
                    "total_count": 0,
                    "error_code": "",
                    "raw_row": {"asin": "DO-NOT-PERSIST-ASIN"},
                }
            ],
            "credentials": "DO-NOT-PERSIST-CREDENTIALS",
        }
    )
    encoded = json.dumps(safe)
    assert "raw_row" not in encoded
    assert "credentials" not in encoded
    assert "DO-NOT-PERSIST" not in encoded

    base = {
        "status": "success",
        "datasets": [
            {
                "dataset": "orders",
                "status": "success",
                "fields": ["amazon_order_id"],
                "sampled_rows": 1,
                "response_count": 1,
                "total_count": 1,
                "error_code": "",
            }
        ],
    }
    for key, bad_value in (
        ("dataset", "../orders"),
        ("status", "leak_raw_data"),
        ("fields", ["amazon-order-id"]),
    ):
        bad = json.loads(json.dumps(base))
        bad["datasets"][0][key] = bad_value
        with pytest.raises(ProbeError):
            validate_probe_result(bad)


def test_error_classifier_returns_only_fixed_codes():
    value = classify_probe_error(
        "orders",
        RuntimeError(
            "403 app_secret=SYNTHETIC-SECRET "
            "https://u:p@example.invalid/?signature=SYNTHETIC-SIGN"
        ),
    )
    encoded = json.dumps(value)
    assert value["status"] == "unauthorized"
    assert value["error_code"] == "unauthorized"
    assert "SYNTHETIC" not in encoded
    assert "example.invalid" not in encoded


def test_probe_service_saves_only_safe_summary_and_preserves_connection_state(tmp_path):
    protector = TestOnlyProtector()
    connection_store = LingxingLocalStore(tmp_path, protector=protector)
    connection_store.save_credentials(credentials())
    connection_store.save_shops(shops())
    connection_store.save_state(
        {
            "status": "success",
            "message": "stable cached shops",
            "last_success_at": "2026-07-28T01:00:00+00:00",
        }
    )
    before = {
        "credentials": connection_store.credentials_path.read_bytes(),
        "shops": connection_store.shops_path.read_bytes(),
        "state": connection_store.state_path.read_bytes(),
    }

    result_store = LingxingProbeResultStore(tmp_path)
    service = LingxingReadOnlyProbeService(
        connection_store, result_store, provider_factory=FakeProbeProvider
    )
    assert service.trigger() is True
    state = wait_for_probe(service)
    assert state["status"] == "success"
    assert state["message_code"] == "probe_completed"
    assert {item["dataset"] for item in state["datasets"]} == {
        "orders",
        "sales_traffic",
    }

    saved = result_store.path.read_text(encoding="utf-8")
    for forbidden in (
        "synthetic-probe-app",
        "synthetic-probe-secret",
        "synthetic-pass",
        "DO-NOT-PERSIST",
        "SELLER-SYNTHETIC-SECRET",
        "MARKETPLACE-SYNTHETIC-SECRET",
    ):
        assert forbidden not in saved
    assert connection_store.credentials_path.read_bytes() == before["credentials"]
    assert connection_store.shops_path.read_bytes() == before["shops"]
    assert connection_store.state_path.read_bytes() == before["state"]


def test_probe_failure_keeps_previous_success(tmp_path):
    protector = TestOnlyProtector()
    connection_store = LingxingLocalStore(tmp_path, protector=protector)
    connection_store.save_credentials(credentials())
    connection_store.save_shops(shops())
    result_store = LingxingProbeResultStore(tmp_path)
    result_store.save(
        {
            "status": "success",
            "last_success_at": "2026-07-28T01:00:00+00:00",
            "datasets": FakeProbeProvider().run_probe(credentials(), shops())["datasets"],
            "message_code": "probe_completed",
        }
    )

    class FailingProbe:
        def run_probe(self, credentials, cached_shops):
            raise RuntimeError("app_secret=SYNTHETIC-SECRET")

    service = LingxingReadOnlyProbeService(
        connection_store, result_store, provider_factory=FailingProbe
    )
    assert service.trigger() is True
    state = wait_for_probe(service)
    assert state["status"] == "failed"
    assert state["last_success_at"] == "2026-07-28T01:00:00+00:00"
    assert len(state["datasets"]) == 2
    assert "SYNTHETIC" not in result_store.path.read_text(encoding="utf-8")


def test_local_probe_http_endpoints_require_same_origin_session(tmp_path):
    data_root = tmp_path / "agent-data"
    protector = TestOnlyProtector()
    seed = LingxingLocalStore(data_root, protector=protector)
    seed.save_credentials(credentials())
    seed.save_shops(shops())

    app = create_integrated_app(
        AgentSettings(data_root=data_root, host="127.0.0.1", port=8766),
        token=TOKEN,
        provider_factory=FakeShopProvider,
        probe_provider_factory=FakeProbeProvider,
        protector=protector,
        start_service=False,
    )
    with TestClient(app) as client:
        assert client.get("/v1/lingxing/probe").status_code == 401
        assert client.get("/lingxing").status_code == 200
        assert client.get(
            "/v1/lingxing/probe", headers=SAME_ORIGIN
        ).status_code == 200
        started = client.post("/v1/lingxing/probe", headers=SAME_ORIGIN)
        assert started.status_code == 202
        deadline = time.monotonic() + 5
        result = None
        while time.monotonic() < deadline:
            result = client.get(
                "/v1/lingxing/probe", headers=SAME_ORIGIN
            ).json()
            if not result["running"]:
                break
            time.sleep(0.02)
        assert result and result["status"] == "success"
        encoded = json.dumps(result, ensure_ascii=False)
        assert "amazon_order_id" in encoded
        for forbidden in (
            "synthetic-probe-app",
            "synthetic-probe-secret",
            "SELLER-SYNTHETIC-SECRET",
            "MARKETPLACE-SYNTHETIC-SECRET",
            "DO-NOT-PERSIST",
        ):
            assert forbidden not in encoded
