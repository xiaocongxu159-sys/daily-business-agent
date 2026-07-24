# -*- coding: utf-8 -*-
"""Real-Chromium synthetic acceptance check for the public local pages."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn
from playwright.sync_api import BrowserContext, Page, expect, sync_playwright

from agent.lingxing_integration import create_integrated_app
from agent.lingxing_secure_store import LingxingCredentials, TestOnlyProtector
from agent.settings import AgentSettings

HOST = "127.0.0.1"
PORT = 8766
BASE_URL = f"http://{HOST}:{PORT}"
TOKEN = "synthetic-browser-token"


class BrowserFakeProvider:
    def list_shops(self, credentials: LingxingCredentials) -> list[dict]:
        if credentials.app_id != "synthetic-browser-app":
            raise AssertionError("unexpected synthetic AppID")
        if credentials.app_secret != "synthetic-browser-secret":
            raise AssertionError("unexpected synthetic AppSecret")
        return [
            {
                "seller_id": "SELLER-BROWSER-1",
                "seller_name": "Synthetic Browser Store",
                "country": "US",
                "region": "NA",
                "status": "active",
                "ads_authorized": True,
            }
        ]


def _wait_for_server(timeout_seconds: float = 15) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("local browser-test server did not start")


def _write_synthetic_inputs(root: Path) -> dict[str, Path]:
    mapping = root / "mapping.csv"
    business = root / "business_20260724.csv"
    advertising = root / "advertising.csv"
    mapping.write_text(
        "shop_id,shop_name,marketplace,parent_asin,seller-sku,MSKU,asin1,item-name,fulfillment-channel,status\n"
        "STORE-BROWSER,Synthetic Browser Store,US,B0PARENT01,SKU-BROWSER,SKU-BROWSER,B0CHILD01,Synthetic Browser Product,FBA,Active\n",
        encoding="utf-8",
    )
    business.write_text(
        "shop_id,marketplace,ASIN,Sessions,Page Views,Units Ordered,Ordered Product Sales,Total Order Items\n"
        "STORE-BROWSER,US,B0CHILD01,120,150,12,239.88,12\n",
        encoding="utf-8",
    )
    advertising.write_text(
        "shop_id,marketplace,Date,Campaign,Ad Group,Advertised ASIN,Advertised SKU,Impressions,Clicks,Spend,Orders,Sales\n"
        "STORE-BROWSER,US,2026-07-24,Synthetic Campaign,Synthetic Group,B0CHILD01,SKU-BROWSER,1000,80,40,6,119.94\n",
        encoding="utf-8",
    )
    return {"mapping": mapping, "business": business, "advertising": advertising}


def _watch_network(context: BrowserContext) -> list[str]:
    external: list[str] = []

    def inspect_request(request) -> None:
        parsed = urlsplit(request.url)
        if parsed.scheme in {"data", "blob", "about"}:
            return
        if parsed.scheme not in {"http", "https"}:
            external.append(request.url)
            return
        if parsed.hostname not in {HOST, "localhost"}:
            external.append(request.url)

    context.on("request", inspect_request)
    return external


def _check_report_page(page: Page, inputs: dict[str, Path]) -> Page:
    page.goto(BASE_URL, wait_until="domcontentloaded")
    expect(page).to_have_title("每日经营数据分析")
    expect(page.locator(".notice")).to_contain_text("只在你电脑")
    page.locator("#input-mapping").set_input_files(str(inputs["mapping"]))
    page.locator("#input-business").set_input_files(str(inputs["business"]))
    page.locator("#input-advertising").set_input_files(str(inputs["advertising"]))
    page.locator("#job-label").fill("Synthetic Browser Acceptance")
    page.locator("#start-analysis").click()
    expect(page.locator("#job-status")).to_have_text("success", timeout=90_000)
    expect(page.locator("#job-step")).to_contain_text("分析完成")
    expect(page.get_by_role("link", name="打开经营看板")).to_be_visible()
    expect(page.get_by_role("link", name="下载完整 Excel")).to_be_visible()

    with page.expect_popup() as popup_info:
        page.get_by_role("link", name="打开经营看板").click()
    dashboard = popup_info.value
    dashboard.wait_for_load_state("domcontentloaded")
    expect(dashboard).to_have_title("每日经营看板")
    expect(dashboard.locator("body")).to_contain_text("Synthetic Browser Product")
    expect(dashboard.locator("body")).to_contain_text("239.88")
    return dashboard


def _check_lingxing_page(page: Page) -> None:
    page.goto(f"{BASE_URL}/lingxing", wait_until="domcontentloaded")
    expect(page).to_have_title("领星自动同步 · 每日经营数据")
    expect(page.locator(".notice")).to_contain_text("数据只保存在这台电脑")
    page.locator("#show-config").click()
    expect(page.locator("#config-panel")).to_have_attribute("open", "")
    page.locator("#app-id").fill("synthetic-browser-app")
    page.locator("#app-secret").fill("synthetic-browser-secret")
    page.locator("#proxy-url").fill("http://127.0.0.1:18080")
    page.locator("#auto-sync").uncheck()
    page.locator("#config-form button[type=submit]").click()
    expect(page.locator("#message")).to_contain_text("连接成功", timeout=15_000)
    expect(page.locator("#app-secret")).to_have_value("")
    expect(page.locator("#proxy-url")).to_have_value("")
    expect(page.locator("#shops")).to_contain_text("Synthetic Browser Store")
    if "synthetic-browser-secret" in page.locator("body").inner_text():
        raise AssertionError("AppSecret remained visible in the Lingxing page")

    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#disconnect").click()
    expect(page.locator("#message")).to_contain_text("已断开", timeout=10_000)
    expect(page.locator("#shops")).to_contain_text("Synthetic Browser Store")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="daily-agent-browser-") as temp:
        root = Path(temp)
        app = create_integrated_app(
            AgentSettings(data_root=root / "agent-data", host=HOST, port=PORT),
            token=TOKEN,
            provider_factory=BrowserFakeProvider,
            protector=TestOnlyProtector(),
            start_service=False,
        )
        server = uvicorn.Server(
            uvicorn.Config(app, host=HOST, port=PORT, log_level="warning", access_log=False)
        )
        thread = threading.Thread(target=server.run, name="browser-test-server", daemon=True)
        thread.start()
        _wait_for_server()
        inputs = _write_synthetic_inputs(root / "inputs")

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(accept_downloads=True)
                external = _watch_network(context)
                page = context.new_page()
                dashboard = _check_report_page(page, inputs)
                _check_lingxing_page(page)
                dashboard.close()
                context.close()
                browser.close()
                if external:
                    raise AssertionError(
                        "non-local browser requests detected:\n" + json.dumps(external, indent=2)
                    )
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            if thread.is_alive():
                raise RuntimeError("browser-test server did not stop cleanly")
    print("browser acceptance passed: report, dashboard, Lingxing, local-only network")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
