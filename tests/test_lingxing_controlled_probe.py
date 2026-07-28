import asyncio
import json
import sys
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace

import agent.lingxing_controlled_probe as controlled
from agent.lingxing_controlled_probe import (
    ControlledValidationProbeProvider,
    SALES_TRAFFIC_OPTIONS_KEY,
    SHARED_INVENTORY_FILTERS,
    classify_controlled_probe_error,
)


class InvalidSignatureError(RuntimeError):
    pass


def test_signature_error_is_not_reported_as_api_unauthorized():
    summary = classify_controlled_probe_error(
        "sales_traffic",
        InvalidSignatureError(
            "错误代码: 2001006\n"
            "request={'seller_id':'SELLER-SECRET','app_secret':'SECRET'}"
        ),
    )
    encoded = json.dumps(summary, ensure_ascii=False)
    assert summary["status"] == "failed"
    assert summary["error_code"] == "request_rejected"
    assert summary["diagnostic_code"] == "sdk_contract_mismatch"
    assert summary["remote_error_code"] == 2001006
    assert "SELLER-SECRET" not in encoded
    assert "app_secret" not in encoded


def test_controlled_probe_uses_one_camel_case_report_shape_and_narrow_inventory(monkeypatch):
    calls = {"inventory": [], "report": []}

    class EmptyResponse:
        data = []
        response_count = 0
        total_count = 0

    class FakeSales:
        async def Listings(self, sid, offset=None, length=None):
            return EmptyResponse()

    class FakeSource:
        async def Orders(self, sid, start, end, date_type=None, offset=None, length=None):
            return EmptyResponse()

    class FakeAds:
        async def AdProfiles(self, profile_type, offset=None, length=None):
            return EmptyResponse()

    class FakeWarehouse:
        async def FbaInventory(self, sid, offset=None, length=None):
            return EmptyResponse()

        async def FbaInventoryDetails(self, **kwargs):
            calls["inventory"].append(dict(kwargs))
            return EmptyResponse()

    class FakeAPI:
        def __init__(self, *args, **kwargs):
            self.sales = FakeSales()
            self.source = FakeSource()
            self.ads = FakeAds()
            self.warehouse = FakeWarehouse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeProxyConnector:
        @classmethod
        def from_url(cls, url):
            return object()

    lingxingapi = ModuleType("lingxingapi")
    lingxingapi.API = FakeAPI
    aiohttp_socks = ModuleType("aiohttp_socks")
    aiohttp_socks.ProxyConnector = FakeProxyConnector
    monkeypatch.setitem(sys.modules, "lingxingapi", lingxingapi)
    monkeypatch.setitem(sys.modules, "aiohttp_socks", aiohttp_socks)

    @contextmanager
    def fake_proxy_endpoint(url):
        yield "http://127.0.0.1:19080"

    async def fake_report(source, request, *, options_key):
        calls["report"].append(
            {
                "options_key": options_key,
                "body": request.to_body(options_key=options_key),
            }
        )
        return {"code": 0, "data": {"task_id": "TASK-SYNTHETIC"}}

    monkeypatch.setattr(controlled, "secure_proxy_endpoint", fake_proxy_endpoint)
    monkeypatch.setattr(controlled, "request_sales_traffic_report", fake_report)

    credentials = SimpleNamespace(
        app_id="APP-SYNTHETIC",
        app_secret="SECRET-SYNTHETIC",
        proxy_url="tls+http://synthetic.invalid",
    )
    shops = [
        {
            "sid": 17,
            "seller_id": "SELLER-SYNTHETIC",
            "marketplace_id": "ATVPDKIKX0DER",
            "region": "NA",
        }
    ]
    result = asyncio.run(ControlledValidationProbeProvider()._run_probe(credentials, shops))

    assert calls["inventory"] == [SHARED_INVENTORY_FILTERS]
    assert calls["report"] and len(calls["report"]) == 1
    assert calls["report"][0]["options_key"] == SALES_TRAFFIC_OPTIONS_KEY == "reportOptions"
    assert "reportOptions" in calls["report"][0]["body"]
    assert "report_options" not in calls["report"][0]["body"]
    by_name = {item["dataset"]: item for item in result["datasets"]}
    assert by_name["sales_traffic"]["status"] == "task_created"
    assert by_name["fba_inventory_shared_detail"]["status"] == "no_data"
