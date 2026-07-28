import asyncio
import sys
from datetime import date, timedelta
from types import ModuleType, SimpleNamespace

from agent.lingxing_available_sync import (
    AvailableLingxingSyncService,
    TlsSdkAvailableDatasetProvider,
    UNAVAILABLE_DATASETS,
    _bounded_window,
)
from agent.lingxing_secure_store import (
    LingxingCredentials,
    LingxingLocalStore,
    TestOnlyProtector,
)
from agent.lingxing_sync_foundation import LingxingDatasetStore


def _response(rows, *, total=None, next_token=""):
    return SimpleNamespace(
        data=list(rows),
        response_count=len(rows),
        total_count=len(rows) if total is None else total,
        next_token=next_token,
    )


def _shop():
    return {
        "mid": 1,
        "sid": 101,
        "seller_id": "SELLER-SYNTHETIC",
        "seller_name": "Synthetic Store",
        "marketplace_id": "ATVPDKIKX0DER",
        "region": "NA",
        "country": "US",
        "status": 1,
        "ads_authorized": True,
    }


def _listing():
    return {
        "sid": 101,
        "asin": "B0SYNTHETIC",
        "parent_asin": "B0PARENT",
        "msku": "MSKU-1",
        "lsku": "LSKU-1",
        "fnsku": "FNSKU-1",
        "product_name": "Synthetic Product",
        "fulfillment_channel": "FBA",
        "status": "Active",
        "deleted": False,
        "update_time_utc": "2026-07-28T00:00:00+00:00",
    }


def _order():
    return {
        "sid": 101,
        "amazon_order_id": "ORDER-1",
        "order_status": "Shipped",
        "asin": "B0SYNTHETIC",
        "msku": "MSKU-1",
        "lsku": "LSKU-1",
        "order_qty": 1,
        "sales_amt": 19.99,
        "currency_code": "USD",
        "purchase_time_utc": "2026-07-27T01:00:00+00:00",
        "purchase_date_loc": "2026-07-26",
        "update_time_ts": 1785114000,
    }


def _profile():
    return {"sid": 101, "profile_id": 9001, "profile_status": 1}


def _sp_row():
    return {
        "campaign_id": 1,
        "ad_group_id": 2,
        "ad_id": 3,
        "asin": "B0SYNTHETIC",
        "msku": "MSKU-1",
        "impressions": 10,
        "clicks": 2,
        "cost": 1.5,
        "orders": 1,
        "sales": 19.99,
    }


def _sb_row():
    return {
        "campaign_id": 11,
        "impressions": 10,
        "clicks": 1,
        "cost": 2.5,
        "orders": 1,
        "sales": 30.0,
    }


def _sd_row():
    return {
        "campaign_id": 21,
        "ad_group_id": 22,
        "ad_id": 23,
        "asin": "B0SYNTHETIC",
        "impressions": 7,
        "clicks": 1,
        "cost": 0.9,
        "orders": 0,
        "sales": 0.0,
    }


def _inventory():
    return {
        "sid": 101,
        "asin": "B0SYNTHETIC",
        "msku": "MSKU-1",
        "lsku": "LSKU-1",
        "fnsku": "FNSKU-1",
        "afn_fulfillable_qty": 10,
        "afn_unsellable_qty": 1,
        "afn_reserved_fc_processing_qty": 2,
        "afn_reserved_fc_transfers_qty": 3,
        "afn_reserved_customer_order_qty": 4,
        "afn_inbound_working_qty": 5,
        "afn_inbound_shipped_qty": 6,
        "afn_inbound_receiving_qty": 7,
    }


def test_initial_and_incremental_windows_are_bounded(tmp_path):
    store = LingxingDatasetStore(tmp_path)
    end_day = date(2026, 7, 28)
    start, end = _bounded_window(
        store,
        "orders",
        initial_days=30,
        overlap_days=3,
        max_days=30,
        end_day=end_day,
    )
    assert start == date(2026, 6, 29)
    assert end == end_day

    store.commit(
        "orders",
        [_order()],
        checkpoint={"date_from": "2026-07-20", "date_to": "2026-07-27"},
    )
    start, end = _bounded_window(
        store,
        "orders",
        initial_days=30,
        overlap_days=3,
        max_days=30,
        end_day=end_day,
    )
    assert start == date(2026, 7, 24)
    assert end == end_day


def test_real_sync_path_excludes_unresolved_sources_and_commits_atomic_datasets(
    tmp_path, monkeypatch
):
    calls = []

    class FakeProxyConnector:
        @classmethod
        def from_url(cls, url):
            calls.append(("proxy", url))
            return object()

    class FakeSales:
        async def Listings(self, sid, offset=None, length=None):
            calls.append(("listings", sid, offset, length))
            return _response([_listing()])

    class FakeSource:
        async def Orders(
            self, sid, start_date, end_date, date_type=None, offset=None, length=None
        ):
            calls.append(
                ("orders", sid, start_date, end_date, date_type, offset, length)
            )
            return _response([_order()])

        def __getattr__(self, name):
            if name in {"ExportReportTask", "ExportReportResult", "_request_with_sign"}:
                raise AssertionError("sales_traffic must not be requested")
            raise AttributeError(name)

    class FakeAds:
        async def AdProfiles(self, profile_type="seller", offset=None, length=None):
            calls.append(("profiles", profile_type, offset, length))
            return _response([_profile()])

        async def SpProductReports(
            self, report_date, sid, profile_id, next_token=None, length=None
        ):
            calls.append(("sp", report_date, sid, profile_id, next_token, length))
            return _response([_sp_row()], next_token="")

        async def SbCampaignReports(
            self, report_date, sid, profile_id, next_token=None, length=None
        ):
            calls.append(("sb", report_date, sid, profile_id, next_token, length))
            return _response([_sb_row()], next_token="")

        async def SdProductReports(
            self, report_date, sid, profile_id, next_token=None, length=None
        ):
            calls.append(("sd", report_date, sid, profile_id, next_token, length))
            return _response([_sd_row()], next_token="")

    class FakeWarehouse:
        async def FbaInventory(self, sid, offset=None, length=None):
            calls.append(("inventory", sid, offset, length))
            return _response([_inventory()])

        def __getattr__(self, name):
            if name == "FbaInventoryDetails":
                raise AssertionError("shared inventory detail must not be requested")
            raise AttributeError(name)

    class FakeAPI:
        def __init__(self, *args, **kwargs):
            assert "proxy_connector" in kwargs
            self.sales = FakeSales()
            self.source = FakeSource()
            self.ads = FakeAds()
            self.warehouse = FakeWarehouse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    socks = ModuleType("aiohttp_socks")
    socks.ProxyConnector = FakeProxyConnector
    sdk = ModuleType("lingxingapi")
    sdk.API = FakeAPI
    monkeypatch.setitem(sys.modules, "aiohttp_socks", socks)
    monkeypatch.setitem(sys.modules, "lingxingapi", sdk)

    credentials = LingxingCredentials(
        "APP-SYNTHETIC",
        "SECRET-SYNTHETIC",
        "http://127.0.0.1:8080",
        auto_sync=False,
    )
    store = LingxingDatasetStore(tmp_path)
    summary = TlsSdkAvailableDatasetProvider().sync(credentials, [_shop()], store)

    assert summary["failed_count"] == 0
    assert set(summary["unavailable"]) == set(UNAVAILABLE_DATASETS)
    for dataset in (
        "shops",
        "listings",
        "orders",
        "ad_profiles",
        "ads_sp_product_daily",
        "ads_sb_campaign_daily",
        "ads_sd_product_daily",
        "fba_inventory_snapshot",
    ):
        snapshot = store.load(dataset)
        assert snapshot is not None
        assert snapshot.rows

    assert store.load("sales_traffic") is None
    assert store.load("fba_inventory_shared_detail") is None
    assert not any(call[0] in {"sales_traffic", "shared_detail"} for call in calls)

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert ("sp", yesterday, 101, 9001, None, 100) in calls
    assert ("sb", yesterday, 101, 9001, None, 100) in calls
    assert ("sd", yesterday, 101, 9001, None, 100) in calls


def test_dataset_failure_keeps_previous_generation_and_hides_exception_text(tmp_path):
    provider = TlsSdkAvailableDatasetProvider()
    store = LingxingDatasetStore(tmp_path)
    store.commit("orders", [_order()])
    previous = store.load("orders")
    assert previous is not None

    async def fail():
        raise RuntimeError(
            "ORDER-SECRET app_secret=SYNTHETIC-SECRET amount=99999"
        )

    results = {}
    asyncio.run(provider._capture(store, results, "orders", fail))
    current = store.load("orders")
    assert current is not None
    assert current.generation == previous.generation
    status = store.load_status("orders")
    encoded = str(status)
    assert status["status"] == "failed"
    assert "ORDER-SECRET" not in encoded
    assert "SYNTHETIC-SECRET" not in encoded
    assert "99999" not in encoded


class FakeShopProvider:
    def list_shops(self, credentials):
        return [_shop()]


class FakeDatasetProvider:
    def sync(self, credentials, cached_shops, store):
        store.commit("shops", [dict(cached_shops[0])], mode="replace")
        return {"failed_count": 0}


def test_service_status_exposes_local_rows_and_explicit_unavailable_sources(tmp_path):
    local_store = LingxingLocalStore(tmp_path, protector=TestOnlyProtector())
    local_store.save_credentials(
        LingxingCredentials(
            "APP-SYNTHETIC",
            "SECRET-SYNTHETIC",
            "http://127.0.0.1:8080",
            auto_sync=False,
        )
    )
    service = AvailableLingxingSyncService(
        local_store,
        provider_factory=FakeShopProvider,
        dataset_provider_factory=FakeDatasetProvider,
    )
    status = service._perform_sync("test")
    assert status["status"] == "success"
    datasets = {item["dataset"]: item for item in status["business_datasets"]}
    assert datasets["shops"]["status"] == "success"
    assert datasets["shops"]["row_count"] == 1
    assert datasets["sales_traffic"] == {
        "dataset": "sales_traffic",
        "status": "unavailable",
        "row_count": 0,
        "last_success_at": None,
        "date_from": None,
        "date_to": None,
        "error_code": "sdk_contract_mismatch",
    }
    assert datasets["fba_inventory_shared_detail"]["error_code"] == (
        "lingxing_server_error"
    )
    assert status["business_storage"] == "local_atomic_generations"
