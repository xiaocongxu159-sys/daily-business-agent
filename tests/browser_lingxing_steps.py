# -*- coding: utf-8 -*-
"""Real-Chromium acceptance for Lingxing sync, probe, and local dashboard."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn
from playwright.sync_api import BrowserContext, expect, sync_playwright

from agent.lingxing_integration import create_integrated_app
from agent.lingxing_secure_store import (
    LingxingCredentials,
    LingxingLocalStore,
    TestOnlyProtector,
)
from agent.settings import AgentSettings
from browser_public_pages import (
    BASE_URL,
    HOST,
    PORT,
    TOKEN,
    _wait_for_server,
    _watch_network,
)
from lingxing_dashboard_fixture import (
    BROWSER_FORBIDDEN_BUYER,
    seed_browser_dashboard_snapshots,
)


class BrowserProbeShopProvider:
    def list_shops(self, credentials: LingxingCredentials) -> list[dict]:
        if credentials.app_id != "synthetic-browser-app":
            raise AssertionError("unexpected synthetic AppID")
        if credentials.app_secret != "synthetic-browser-secret":
            raise AssertionError("unexpected synthetic AppSecret")
        return [
            {
                "sid": 501,
                "seller_id": "SELLER-BROWSER-1",
                "seller_name": "Synthetic Browser Store",
                "marketplace_id": "MARKETPLACE-BROWSER-1",
                "country": "US",
                "region": "NA",
                "status": "active",
                "ads_authorized": True,
            }
        ]


class BrowserFakeProbeProvider:
    def run_probe(self, credentials, cached_shops):
        if credentials.app_secret != "synthetic-browser-secret":
            raise AssertionError("probe did not receive encrypted credentials")
        if not cached_shops or cached_shops[0].get("sid") != 501:
            raise AssertionError("probe did not receive the cached shop identity")
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
                    "total_count": 28,
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
                {
                    "dataset": "fba_inventory_shared_detail",
                    "status": "failed",
                    "fields": [],
                    "sampled_rows": 0,
                    "response_count": 0,
                    "total_count": None,
                    "date_from": None,
                    "date_to": None,
                    "error_code": "invalid_response",
                    "diagnostic_code": "sdk_response_validation",
                    "remote_error_code": None,
                    "raw_error": "ASIN-BROWSER-MUST-NOT-APPEAR",
                },
            ],
            "raw_order": "ORDER-BROWSER-MUST-NOT-APPEAR",
            "raw_amount": "98765.43",
        }


def _check_dashboard(page) -> None:
    expect(page).to_have_title("每日经营看板")
    expect(page.locator("#source")).to_contain_text("本机领星同步快照")
    expect(page.locator("#availabilityNotice")).to_be_visible()
    expect(page.locator("#availabilityNotice")).to_contain_text("暂不可用")
    expect(page.locator("#cards")).to_contain_text("59.97")
    expect(page.locator("#cards")).to_contain_text("7.75")
    expect(page.locator("#cards")).to_contain_text("含店铺级未分配广告")
    expect(page.locator("#trafficChart")).to_contain_text("不会将缺失数据绘制为 0")
    expect(page.locator("#salesChart svg")).to_be_visible()
    expect(page.locator("#adsChart svg")).to_be_visible()
    expect(page.locator("#inventoryChart svg")).to_be_visible()
    expect(page.locator('[data-axis-upright="1"]')).to_have_count(5)
    expect(page.locator('[data-chart-tooltip="1"]')).to_have_count(3)
    expect(page.locator("#body")).to_contain_text("2026-07-29")
    expect(page.locator("#body")).to_contain_text("12")
    expect(page.locator("#quality")).to_contain_text("Sessions/PV 暂不可用")
    expect(page.locator("#quality")).to_contain_text("店铺级广告总额和趋势")

    body = page.locator("body").inner_text()
    for forbidden in (
        BROWSER_FORBIDDEN_BUYER,
        "synthetic-browser-secret",
        "browser-pass",
        "SELLER-BROWSER-PRIVATE",
    ):
        if forbidden in body:
            raise AssertionError(f"local dashboard leaked forbidden value: {forbidden}")

    page.locator("#adsChart").hover(position={"x": 220, "y": 150})
    expect(page.locator("#adsChart [data-chart-tooltip]")).to_be_visible()
    page.mouse.wheel(0, 500)
    if page.evaluate("window.scrollY") <= 0:
        raise AssertionError("mouse wheel did not scroll the generated dashboard")


def run_page_check() -> None:
    with tempfile.TemporaryDirectory(prefix="daily-agent-lingxing-page-") as temp:
        root = Path(temp)
        data_root = root / "agent-data"
        protector = TestOnlyProtector()
        store = LingxingLocalStore(data_root, protector=protector)
        store.save_credentials(
            LingxingCredentials(
                app_id="synthetic-browser-app",
                app_secret="synthetic-browser-secret",
                proxy_url="tls+http://browser-user:browser-pass@192.0.2.50:8443?fingerprint="
                + "ab" * 32,
                auto_sync=False,
                sync_interval_minutes=120,
            )
        )
        store.save_shops(BrowserProbeShopProvider().list_shops(store.load_credentials()))
        seed_browser_dashboard_snapshots(data_root)

        app = create_integrated_app(
            AgentSettings(data_root=data_root, host=HOST, port=PORT),
            token=TOKEN,
            provider_factory=BrowserProbeShopProvider,
            probe_provider_factory=BrowserFakeProbeProvider,
            protector=protector,
            start_service=False,
        )
        server = uvicorn.Server(
            uvicorn.Config(app, host=HOST, port=PORT, log_level="warning", access_log=False)
        )
        thread = threading.Thread(target=server.run, name="lingxing-browser-server", daemon=True)
        thread.start()
        _wait_for_server()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context: BrowserContext = browser.new_context()
                external = _watch_network(context)
                page = context.new_page()
                response = page.goto(f"{BASE_URL}/lingxing", wait_until="domcontentloaded")
                if response is None or response.status != 200:
                    raise AssertionError("Lingxing page did not return 200")
                expect(page).to_have_title("领星自动同步 · 每日经营数据")
                expect(page.locator("#show-config")).to_be_visible()
                expect(page.locator("#package-ready")).to_be_attached()
                expect(page.locator("#save-config")).to_be_disabled()
                expect(page.locator("#probe-card")).to_be_visible()
                expect(page.locator("#probe-now")).to_be_enabled(timeout=10_000)
                expect(page.locator("#probe-card")).to_contain_text("不会显示或保存")
                expect(page.locator("#probe-card")).to_contain_text("安全诊断")
                expect(page.locator("#dashboard-card")).to_be_visible()
                expect(page.locator("#dashboard-card")).to_contain_text("不会重新访问领星")
                expect(page.locator("#dashboard-now")).to_be_enabled(timeout=15_000)
                if page.locator('input[type="file"]').count():
                    raise AssertionError("single-file native flow must not expose browser file input")
                if page.locator("#proxy-url").count():
                    raise AssertionError("technical proxy field must remain hidden")

                page.locator("#dashboard-now").click()
                expect(page.locator("#dashboard-pill")).to_have_text("已生成", timeout=45_000)
                expect(page.locator("#dashboard-open")).to_be_visible()
                dashboard_href = page.locator("#dashboard-open").get_attribute("href")
                if not dashboard_href:
                    raise AssertionError("generated dashboard link is missing")
                page.goto(f"{BASE_URL}{dashboard_href}", wait_until="domcontentloaded")
                _check_dashboard(page)

                page.goto(f"{BASE_URL}/lingxing", wait_until="domcontentloaded")
                expect(page.locator("#probe-now")).to_be_enabled(timeout=10_000)
                page.locator("#probe-now").click()
                expect(page.locator("#probe-pill")).to_have_text("已完成", timeout=15_000)
                order_row = page.locator('[data-probe-dataset="orders"]')
                expect(order_row).to_be_visible()
                expect(order_row).to_contain_text("amazon_order_id")
                expect(order_row).to_contain_text("sales_amt")
                expect(order_row).to_contain_text("2026-07-27")
                traffic_row = page.locator('[data-probe-dataset="sales_traffic"]')
                expect(traffic_row).to_contain_text("任务已接受")
                diagnostic_row = page.locator(
                    '[data-probe-dataset="fba_inventory_shared_detail"]'
                )
                expect(diagnostic_row).to_contain_text("sdk_response_validation")

                probe_text = page.locator("#probe-results").inner_text()
                for forbidden in (
                    "synthetic-browser-secret",
                    "browser-pass",
                    "ORDER-BROWSER-MUST-NOT-APPEAR",
                    "ASIN-BROWSER-MUST-NOT-APPEAR",
                    "98765.43",
                    "MARKETPLACE-BROWSER-1",
                ):
                    if forbidden in probe_text:
                        raise AssertionError(f"probe UI leaked forbidden value: {forbidden}")
                if external:
                    raise AssertionError(
                        "non-local browser requests detected:\n" + json.dumps(external, indent=2)
                    )
                context.close()
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            if thread.is_alive():
                raise RuntimeError("Lingxing browser-test server did not stop cleanly")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", choices=("page",), required=True)
    parser.parse_args()
    run_page_check()
    print("Lingxing browser acceptance passed: local-only sync, probe, and dashboard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
